"""初始化函数，参考原始 init.ts 的设计。

有序启动基础设施，memoized 保证只执行一次。

初始化顺序：
  0. load_dotenv() — 加载 .env 文件
  1. enable_configs() — 加载配置
  2. apply_config_environment_variables() — 应用配置环境变量
  3. 设置 LLM 客户端（延迟，不在此处构建）
  4. 初始化遥测（可选）
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from startup.config import (
    apply_config_environment_variables,
    enable_configs,
)

# ---------------------------------------------------------------------------
# Memoized init
# ---------------------------------------------------------------------------

_init_lock = threading.Lock()
_init_done: bool = False


def init() -> None:
    """有序启动基础设施，memoized（只执行一次）。

    初始化顺序：
      0. load_dotenv() — 加载 .env 文件到环境变量
      1. enable_configs() — 加载配置
      2. apply_config_environment_variables() — 应用配置环境变量
      3. （预留）LLM 客户端延迟初始化
      4. （预留）遥测初始化
    """
    global _init_done

    with _init_lock:
        if _init_done:
            return
        _init_done = True

    # 0. 加载 .env 文件（从项目根目录查找）
    _load_env()

    # 1. 加载配置
    enable_configs()

    # 2. 应用配置环境变量
    env_vars = apply_config_environment_variables()
    for key, value in env_vars.items():
        os.environ.setdefault(key, value)

    # 3. LLM 客户端 - 延迟，不在此处构建
    # 4. 遥测 - 可选，暂不实现
    # 5. 检查 Jasper embedding 模型是否已下载，未下载则自动下载
    _ensure_embedding_model()


def _load_env() -> None:
    """从项目根目录加载 .env 文件到环境变量。

    查找策略：从当前工作目录向上查找 .env 文件。
    不覆盖已存在的环境变量（override=False）。
    """
    # 从当前目录开始查找 .env
    env_path = Path.cwd() / ".env"
    if env_path.exists():
        load_dotenv(env_path, override=False)
    else:
        # 回退：尝试从脚本所在目录的上级查找
        try:
            project_root = Path(__file__).resolve().parent.parent
            env_path = project_root / ".env"
            if env_path.exists():
                load_dotenv(env_path, override=False)
        except Exception:
            pass


def is_initialized() -> bool:
    """检查是否已初始化。"""
    return _init_done


def _ensure_embedding_model() -> None:
    """检查 Jasper embedding 模型是否已下载，未下载则自动下载。

    在 init() 中调用，安装后首次启动时自动下载模型。
    后续启动时模型已存在，直接跳过。
    """
    try:
        from memory.embedding.download import is_model_downloaded, download_model

        if is_model_downloaded():
            return

        logger.info("Jasper embedding 模型未下载，开始自动下载...")
        success = download_model()
        if not success:
            logger.warning(
                "Jasper 模型自动下载失败，embedding 将降级为纯 BM25 模式。"
                "可稍后运行 download-embedding-model 命令手动下载。"
            )
    except Exception as e:
        logger.warning("embedding 模型检查失败: %s", e)


def reset_init_for_tests() -> None:
    """重置初始化状态（仅用于测试）。"""
    global _init_done
    with _init_lock:
        _init_done = False
