"""启动入口：基础设施初始化 + 会话状态搭建。

合并了原 init() 与 setup() 的职责，server 启动时调用一次即可。
依次完成：加载环境变量 -> 开启配置系统 -> 应用配置环境变量 ->
确保 embedding 模型 -> 设置工作目录 -> 查找 git 根 -> 捕获 hooks 快照 ->
设置权限模式 -> 构造 AppState。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from startup.bootstrap.state import (
    get_model,
    set_cwd_state,
    set_original_cwd,
    set_permission_mode,
    set_project_root,
)
from startup.config import (
    _ensure_config_file,
    apply_config_environment_variables,
    enable_configs,
    get_project_settings_path,
)
from startup.hooks import HookConfig, capture_hooks_config_snapshot
from startup.state.app_state import AppState, AppStateProvider

logger = logging.getLogger(__name__)

# 模块级 hooks 快照缓存
_hooks_snapshot: HookConfig | None = None


def _load_env() -> None:
    """从项目根目录加载 .env 文件，不覆盖已有环境变量。"""
    env_path = Path.cwd() / ".env"
    if env_path.exists():
        load_dotenv(env_path, override=False)
        return
    # 回退：从脚本所在目录的上级查找
    project_root = Path(__file__).resolve().parent.parent
    env_path = project_root / ".env"
    if env_path.exists():
        load_dotenv(env_path, override=False)


def _ensure_embedding_model() -> None:
    """检查 Jasper embedding 模型是否已下载。

    未下载时不自动下载（避免启动时静默拉取约 1.2GB 模型），
    仅提示显式下载入口；缺失时降级为纯 BM25 模式，不阻断启动。
    """
    try:
        from memory.embedding.download import is_model_downloaded

        if is_model_downloaded():
            return

        logger.info(
            "Jasper embedding 模型未下载，语义检索降级为纯 BM25。"
            "需要语义检索时请运行 download-embedding-model 命令下载（约 1.2GB）。"
        )
    except Exception as e:
        logger.warning("embedding 模型检查失败: %s", e)


def find_git_root(path: str) -> str | None:
    """从 path 向上查找 .git 目录，返回 git 仓库根路径。"""
    current = Path(path).resolve()
    while True:
        if (current / ".git").exists():
            return str(current)
        parent = current.parent
        if parent == current:
            return None
        current = parent


async def setup(
    cwd: str | None = None,
    permission_mode: str = "default",
    **kwargs: Any,
) -> AppStateProvider:
    """启动入口：基础设施初始化 + 会话状态搭建。

    Args:
        cwd: 工作目录，默认为 os.getcwd()
        permission_mode: 权限模式，默认 "default"
        **kwargs: 预留扩展

    Returns:
        初始化后的 AppStateProvider 实例
    """
    global _hooks_snapshot

    # ---- 基础设施初始化 ----
    _load_env()
    enable_configs()
    for key, value in apply_config_environment_variables().items():
        os.environ.setdefault(key, value)
    _ensure_embedding_model()

    # ---- 工作目录 ----
    normalized = os.path.normpath(cwd or os.getcwd())
    set_cwd_state(normalized)
    set_original_cwd(normalized)
    set_project_root(normalized)
    try:
        os.chdir(normalized)
    except OSError as e:
        logger.warning("chdir 失败: %s - %s", normalized, e)
    logger.info("工作目录设置为: %s", normalized)

    # ---- git 根 ----
    git_root = find_git_root(normalized)
    if git_root:
        logger.info("Git 根目录: %s", git_root)

    # ---- hooks 快照（必须在 set_cwd 之后，确保从正确目录加载）----
    _ensure_config_file(get_project_settings_path())
    _hooks_snapshot = capture_hooks_config_snapshot()
    logger.info(
        "Hooks 快照已捕获: pre=%d, post=%d",
        len(_hooks_snapshot.pre_tool_use),
        len(_hooks_snapshot.post_tool_use),
    )

    # ---- 权限模式 + AppState ----
    set_permission_mode(permission_mode)
    return AppStateProvider(AppState(model=get_model()))


def get_hooks_snapshot() -> HookConfig | None:
    """获取当前 hooks 配置快照。"""
    return _hooks_snapshot


def update_hooks_snapshot() -> None:
    """重新捕获 hooks 配置快照（插件加载后或工作目录变更后调用）。"""
    global _hooks_snapshot
    _hooks_snapshot = capture_hooks_config_snapshot()
