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
