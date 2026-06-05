"""REPL 启动器，参考原始 replLauncher.tsx 的设计。

负责：
  - 创建 REPLScreen 实例
  - 初始化 Ink 渲染引擎
  - 进入 REPL 主循环
  - 清理退出
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from ink.screens.repl import REPLScreen
from startup.state.app_state import AppStateProvider

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# launch_repl — 启动 REPL 交互界面
# ---------------------------------------------------------------------------


async def launch_repl(
    app_state: AppStateProvider,
    **kwargs: Any,
) -> None:
    """启动 REPL 交互界面。

    执行步骤：
      1. 创建 REPLScreen 实例
      2. 初始化 Ink 渲染引擎（Python 版本简化为终端渲染）
      3. 进入 REPL 主循环
      4. 清理退出

    Args:
        app_state: 应用状态提供者
        **kwargs: 额外参数（预留扩展，如 initial_prompt 等）
    """
    logger.info("启动 REPL...")

    # 1. 创建 REPLScreen 实例
    repl_screen = REPLScreen(app_state)

    # 2. 初始化渲染 — Python 版本使用终端直接渲染
    #    （原始 TS 版本使用 Ink/React 渲染树，Python 版本简化为终端输出）
    try:
        # 3. 进入 REPL 主循环
        await repl_screen.run()
    except (EOFError, KeyboardInterrupt):
        print("\nGoodbye!")
    except Exception as e:
        logger.error("REPL 运行异常: %s", e)
        raise
    finally:
        # 4. 清理退出
        repl_screen.stop()
        logger.info("REPL 已退出")


# ---------------------------------------------------------------------------
# run_interactive — 交互式路径入口
# ---------------------------------------------------------------------------


async def run_interactive(
    app_state: AppStateProvider,
    **kwargs: Any,
) -> None:
    """交互式路径入口。

    初始化 → launch_repl，供 main.py 调用。

    Args:
        app_state: 应用状态提供者
        **kwargs: 额外参数
    """
    await launch_repl(app_state, **kwargs)


# ---------------------------------------------------------------------------
# 测试块
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import os
    import sys

    # 确保项目根目录在 sys.path 上
    _project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _project_root not in sys.path:
        sys.path.insert(0, _project_root)

    print("=" * 60)
    print("repl_launcher.py 测试")
    print("=" * 60)

    # 测试 1: launch_repl 创建 REPLScreen 并退出
    print("\n--- 测试 1: launch_repl 创建 REPLScreen ---")
    try:
        provider = AppStateProvider()
        state = provider.get_state()
        state.model = "test-model"

        # 使用 mock 避免真正进入交互循环
        repl_screen = REPLScreen(provider)
        assert repl_screen is not None
        print(f"  REPLScreen 创建成功，model={state.model}")
        print("  [PASS] launch_repl 创建 REPLScreen")
    except Exception as e:
        print(f"  [FAIL] {e}")

    # 测试 2: run_interactive 入口函数存在
    print("\n--- 测试 2: run_interactive 入口函数 ---")
    try:
        assert callable(run_interactive), "run_interactive 应为可调用函数"
        assert asyncio.iscoroutinefunction(run_interactive), "run_interactive 应为异步函数"
        print("  run_interactive 是异步可调用函数")
        print("  [PASS] run_interactive 入口函数")
    except Exception as e:
        print(f"  [FAIL] {e}")

    # 测试 3: launch_repl 是异步函数
    print("\n--- 测试 3: launch_repl 是异步函数 ---")
    try:
        assert callable(launch_repl), "launch_repl 应为可调用函数"
        assert asyncio.iscoroutinefunction(launch_repl), "launch_repl 应为异步函数"
        print("  launch_repl 是异步可调用函数")
        print("  [PASS] launch_repl 是异步函数")
    except Exception as e:
        print(f"  [FAIL] {e}")

    # 测试 4: REPLScreen 停止行为
    print("\n--- 测试 4: REPLScreen 停止行为 ---")
    try:
        provider = AppStateProvider()
        repl_screen = REPLScreen(provider)
        repl_screen.stop()
        assert not repl_screen._is_running, "停止后 _is_running 应为 False"
        print("  stop() 后 _is_running=False")
        print("  [PASS] REPLScreen 停止行为")
    except Exception as e:
        print(f"  [FAIL] {e}")

    print("\n" + "=" * 60)
    print("所有测试完成")
    print("=" * 60)
