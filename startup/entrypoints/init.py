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

from startup.utils.config import (
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

    # 3. LLM 客户端 — 延迟，不在此处构建
    # 4. 遥测 — 可选，暂不实现


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


def reset_init_for_tests() -> None:
    """重置初始化状态（仅用于测试）。"""
    global _init_done
    with _init_lock:
        _init_done = False


# ---------------------------------------------------------------------------
# 测试块
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 60)
    print("init.py 测试")
    print("=" * 60)

    # 测试 1: init() 正常执行
    print("\n--- 测试 1: init() 正常执行 ---")
    try:
        assert not is_initialized(), "初始状态应为未初始化"
        init()
        assert is_initialized(), "init() 后应为已初始化"
        print("  [PASS] init() 正常执行")
    except Exception as e:
        print(f"  [FAIL] {e}")

    # 测试 2: init() memoize 行为 — 多次调用只执行一次
    print("\n--- 测试 2: init() memoize 行为 ---")
    try:
        reset_init_for_tests()
        assert not is_initialized()
        init()
        assert is_initialized()
        # 再次调用应无副作用
        init()
        assert is_initialized()
        print("  [PASS] init() memoize 行为正确")
    except Exception as e:
        print(f"  [FAIL] {e}")

    # 测试 3: is_initialized() 状态检查
    print("\n--- 测试 3: is_initialized() 状态检查 ---")
    try:
        reset_init_for_tests()
        assert is_initialized() is False
        init()
        assert is_initialized() is True
        print("  [PASS] is_initialized() 状态正确")
    except Exception as e:
        print(f"  [FAIL] {e}")

    # 测试 4: reset_init_for_tests()
    print("\n--- 测试 4: reset_init_for_tests() ---")
    try:
        assert is_initialized()
        reset_init_for_tests()
        assert not is_initialized()
        print("  [PASS] reset_init_for_tests() 正确重置")
    except Exception as e:
        print(f"  [FAIL] {e}")

    print("\n" + "=" * 60)
    print("所有测试完成")
    print("=" * 60)
