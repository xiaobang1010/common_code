"""会话级状态搭建，参考原始 setup.ts 的设计。

在交互式/非交互式会话启动时执行，完成以下步骤：
  1. setCwd — 设置工作目录到 bootstrap state
  2. find_git_root — 查找 git 根目录
  3. capture_hooks_config_snapshot — 捕获 hooks 快照
  4. 初始化权限模式
  5. 设置初始 AppState
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from startup.bootstrap.state import (
    get_cwd_state,
    get_model,
    get_session_id,
    set_cwd_state,
    set_original_cwd,
    set_permission_mode,
    set_project_root,
)
from startup.state.app_state import AppState, AppStateProvider
from startup.hooks import capture_hooks_config_snapshot, HookConfig

logger = logging.getLogger(__name__)

# 模块级 hooks 快照缓存
_hooks_snapshot: HookConfig | None = None


# ---------------------------------------------------------------------------
# set_cwd — 设置工作目录
# ---------------------------------------------------------------------------


def set_cwd(cwd: str) -> None:
    """设置工作目录到 bootstrap state。

    同时更新 os.getcwd() 和 bootstrap state 中的 cwd/original_cwd/project_root。

    Args:
        cwd: 目标工作目录路径
    """
    normalized = os.path.normpath(cwd)
    set_cwd_state(normalized)
    set_original_cwd(normalized)
    set_project_root(normalized)
    try:
        os.chdir(normalized)
    except OSError as e:
        logger.warning("chdir 失败: %s — %s", normalized, e)


# ---------------------------------------------------------------------------
# find_git_root — 查找 git 根目录
# ---------------------------------------------------------------------------


def find_git_root(path: str) -> str | None:
    """从 path 向上查找 .git 目录，返回 git 仓库根路径。

    Args:
        path: 起始查找路径

    Returns:
        git 根目录路径，如果未找到返回 None
    """
    current = Path(path).resolve()
    while True:
        git_dir = current / ".git"
        if git_dir.exists():
            return str(current)
        parent = current.parent
        if parent == current:
            # 已到文件系统根
            return None
        current = parent


# ---------------------------------------------------------------------------
# setup — 会话级状态搭建
# ---------------------------------------------------------------------------


async def setup(
    cwd: str | None = None,
    permission_mode: str = "default",
    **kwargs: Any,
) -> AppStateProvider:
    """会话级状态搭建。

    执行顺序：
      1. setCwd(cwd or os.getcwd()) — 设置工作目录
      2. find_git_root() — 查找 git 根目录
      3. capture_hooks_config_snapshot() — 捕获 hooks 快照
      4. 初始化权限模式
      5. 设置初始 AppState

    Args:
        cwd: 工作目录，默认为 os.getcwd()
        permission_mode: 权限模式，默认 "default"
        **kwargs: 额外参数（预留扩展）

    Returns:
        初始化后的 AppStateProvider 实例
    """
    global _hooks_snapshot

    # 1. 设置工作目录
    resolved_cwd = cwd or os.getcwd()
    set_cwd(resolved_cwd)
    logger.info("工作目录设置为: %s", resolved_cwd)

    # 2. 查找 git 根目录
    git_root = find_git_root(resolved_cwd)
    if git_root:
        logger.info("Git 根目录: %s", git_root)
    else:
        logger.info("未找到 Git 仓库")

    # 3. 捕获 hooks 配置快照
    #    IMPORTANT: 必须在 setCwd() 之后调用，确保 hooks 从正确目录加载

    # 确保项目设置文件存在
    from startup.config import get_project_settings_path, _ensure_config_file
    _ensure_config_file(get_project_settings_path())

    _hooks_snapshot = capture_hooks_config_snapshot()
    logger.info(
        "Hooks 快照已捕获: pre=%d, post=%d",
        len(_hooks_snapshot.pre_tool_use),
        len(_hooks_snapshot.post_tool_use),
    )

    # 4. 初始化权限模式
    set_permission_mode(permission_mode)
    logger.info("权限模式: %s", permission_mode)

    # 5. 设置初始 AppState
    app_state = AppState(
        session_id=get_session_id(),
        model=get_model(),
        permission_mode=permission_mode,
    )
    provider = AppStateProvider(app_state)

    return provider


# ---------------------------------------------------------------------------
# Hooks 快照访问
# ---------------------------------------------------------------------------


def get_hooks_snapshot() -> HookConfig | None:
    """获取当前 hooks 配置快照。"""
    return _hooks_snapshot


def update_hooks_snapshot() -> None:
    """重新捕获 hooks 配置快照（工作目录变更后调用）。"""
    global _hooks_snapshot
    _hooks_snapshot = capture_hooks_config_snapshot()
