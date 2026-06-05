"""主入口编排，参考原始 main.tsx 的交互式启动流程。

提供：
  - run_interactive_mode: 交互式模式主入口
  - run_non_interactive_mode: 非交互模式
  - show_setup_screens: 设置对话框序列
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from typing import Any

from startup.bootstrap.state import (
    get_cwd_state,
    set_is_interactive,
)
from startup.entrypoints.init import init
from ink.repl_launcher import launch_repl, run_interactive
from startup.setup import setup
from startup.state.app_state import AppStateProvider
from startup.utils.config import get_global_config

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# show_setup_screens — 设置对话框序列
# ---------------------------------------------------------------------------


async def show_setup_screens(app_state: AppStateProvider) -> bool:
    """设置对话框序列。

    参考 interactiveHelpers.tsx 的 showSetupScreens()，简化实现：
      - 检查是否首次使用 → 显示欢迎信息
      - 检查是否新项目 → 显示信任对话框
      - 检查是否需要权限设置

    Python 版本目前使用终端文本交互，而非 Ink/React 渲染。

    Args:
        app_state: 应用状态提供者

    Returns:
        是否显示了 onboarding 对话框
    """
    config = get_global_config()
    onboarding_shown = False

    # 1. 首次使用检查
    if not config.has_completed_onboarding:
        onboarding_shown = True
        _show_welcome()

    # 2. 项目信任检查（简化版）
    #    原始 TS 版本使用 TrustDialog React 组件
    #    Python 版本使用终端文本提示
    _check_project_trust(app_state)

    # 3. 权限设置检查
    state = app_state.get_state()
    if state.bypass_permissions:
        _show_bypass_permissions_warning()

    return onboarding_shown


def _show_welcome() -> None:
    """显示欢迎信息。"""
    print("\x1b[1m欢迎使用 Common Code Python Edition!\x1b[0m")
    print()
    print("  这是一个基于 Python 的 Common Code 实现。")
    print("  输入 /help 查看可用命令。")
    print()


def _check_project_trust(app_state: AppStateProvider) -> None:
    """检查项目信任状态。

    简化实现：在终端中显示当前工作目录信息。
    原始 TS 版本使用 TrustDialog 组件进行交互式确认。
    """
    cwd = get_cwd_state()
    logger.info("项目目录: %s", cwd)


def _show_bypass_permissions_warning() -> None:
    """显示绕过权限模式警告。"""
    print("\x1b[33m警告: 正在以绕过权限模式运行。\x1b[0m")
    print("  所有工具调用将自动批准，请确保在安全环境中使用。")
    print()


# ---------------------------------------------------------------------------
# run_interactive_mode — 交互式模式主入口
# ---------------------------------------------------------------------------


async def run_interactive_mode(args: dict | None = None) -> None:
    """交互式模式主入口。

    执行顺序：
      1. init() — 基础设施初始化
      2. setup(cwd, permission_mode, ...) — 会话状态搭建
      3. show_setup_screens() — 设置对话框序列
      4. launch_repl() — 启动 REPL

    Args:
        args: CLI 参数字典，支持以下键：
            - cwd: 工作目录
            - permission_mode: 权限模式
            - model: 模型名称
            - verbose: 详细模式
    """
    args = args or {}

    # 标记为交互式会话
    set_is_interactive(True)

    # 1. 基础设施初始化
    logger.info("初始化基础设施...")
    init()

    # 2. 会话状态搭建
    logger.info("搭建会话状态...")
    app_state = await setup(
        cwd=args.get("cwd"),
        permission_mode=args.get("permission_mode", "default"),
    )

    # 应用额外参数到 AppState
    if args.get("model"):
        from startup.bootstrap.state import set_model
        set_model(args["model"])
        app_state.set_state(lambda s: _set_model(s, args["model"]))

    if args.get("verbose"):
        from startup.bootstrap.state import set_verbose
        set_verbose(True)
        app_state.set_state(lambda s: _set_verbose(s, True))

    # 3. 设置对话框序列
    logger.info("显示设置对话框...")
    await show_setup_screens(app_state)

    # 4. 启动 REPL
    logger.info("启动 REPL...")
    await launch_repl(app_state)


# ---------------------------------------------------------------------------
# run_non_interactive_mode — 非交互模式
# ---------------------------------------------------------------------------


async def run_non_interactive_mode(
    prompt: str,
    args: dict | None = None,
) -> None:
    """非交互模式。

    执行顺序：
      1. init() — 基础设施初始化
      2. setup() — 会话状态搭建
      3. query(prompt) — 执行查询
      4. 输出结果 → 退出

    Args:
        prompt: 用户提示文本
        args: CLI 参数字典
    """
    args = args or {}

    # 标记为非交互式会话
    set_is_interactive(False)

    # 1. 基础设施初始化
    init()

    # 2. 会话状态搭建
    app_state = await setup(
        cwd=args.get("cwd"),
        permission_mode=args.get("permission_mode", "default"),
    )

    # 应用额外参数
    if args.get("model"):
        from startup.bootstrap.state import set_model
        set_model(args["model"])
        app_state.set_state(lambda s: _set_model(s, args["model"]))

    # 3. 执行查询
    from query.services.api.client import get_llm_client, get_default_model
    from query.services.api.llm import query_model_with_streaming

    model = args.get("model") or get_default_model()
    messages = [{"role": "user", "content": prompt}]

    full_text = ""
    async for event in query_model_with_streaming(messages=messages, tools=None, model=model):
        if event.type == "content":
            full_text += (event.content or "")
            print(event.content or "", end="", flush=True)
        elif event.type == "done":
            print()  # 换行
        elif event.type == "usage":
            logger.info("Token usage: %s", event.usage)
        elif event.type == "error":
            print(f"\nError: {event.content}", file=sys.stderr)

    if not full_text:
        print("No response received.", file=sys.stderr)


# ---------------------------------------------------------------------------
# AppState 更新辅助函数
# ---------------------------------------------------------------------------


def _set_model(state: Any, model: str) -> Any:
    """更新 AppState 的 model 字段。"""
    state.model = model
    return state


def _set_verbose(state: Any, verbose: bool) -> Any:
    """更新 AppState 的 verbose 字段。"""
    state.verbose = verbose
    return state


# ---------------------------------------------------------------------------
# 测试块
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import io
    import sys
    import tempfile
    from contextlib import redirect_stdout

    # 确保项目根目录在 sys.path 上（支持 from state.xxx 等非限定导入）
    _project_root = os.path.dirname(os.path.abspath(__file__))
    if _project_root not in sys.path:
        sys.path.insert(0, _project_root)

    print("=" * 60)
    print("main.py 测试")
    print("=" * 60)

    # 测试 1: setup() 设置工作目录
    print("\n--- 测试 1: setup() 设置工作目录 ---")
    try:
        from startup.bootstrap.state import reset_state_for_tests
        reset_state_for_tests()

        provider = asyncio.run(setup(cwd=os.getcwd()))
        state = provider.get_state()
        assert state.session_id is not None, "session_id 应已设置"
        assert get_cwd_state() == os.path.normpath(os.getcwd()), "cwd 应已设置"
        print(f"  session_id = {state.session_id}")
        print(f"  cwd = {get_cwd_state()}")
        print("  [PASS] setup() 设置工作目录")
    except Exception as e:
        print(f"  [FAIL] {e}")

    # 测试 2: find_git_root()
    print("\n--- 测试 2: find_git_root() ---")
    try:
        from startup.setup import find_git_root
        cwd = os.getcwd()
        git_root = find_git_root(cwd)
        if git_root:
            print(f"  git 根目录: {git_root}")
            assert (os.path.exists(os.path.join(git_root, ".git"))), "应包含 .git"
        else:
            print("  当前目录不在 git 仓库中")
        print("  [PASS] find_git_root()")
    except Exception as e:
        print(f"  [FAIL] {e}")

    # 测试 3: run_interactive_mode 流程（使用 mock）
    print("\n--- 测试 3: run_interactive_mode 流程（使用 mock）---")
    try:
        from unittest.mock import AsyncMock, patch

        from startup.bootstrap.state import reset_state_for_tests
        reset_state_for_tests()

        # Mock launch_repl 避免真正进入交互循环
        # 使用 sys.modules[__name__] 获取当前模块（支持 python -m 运行）
        import types
        current_module = sys.modules[__name__]
        original_launch_repl = current_module.launch_repl
        mock_launch = AsyncMock()
        current_module.launch_repl = mock_launch

        try:
            asyncio.run(run_interactive_mode(args={
                "cwd": os.getcwd(),
                "permission_mode": "default",
                "model": "test-model",
            }))

            # 验证 launch_repl 被调用
            assert mock_launch.called, "launch_repl 应被调用"
            call_args = mock_launch.call_args
            app_state_arg = call_args[0][0]
            assert isinstance(app_state_arg, AppStateProvider), "参数应为 AppStateProvider"
            print(f"  launch_repl 被调用，model={app_state_arg.get_state().model}")
            print("  [PASS] run_interactive_mode 流程")
        finally:
            current_module.launch_repl = original_launch_repl
    except Exception as e:
        print(f"  [FAIL] {e}")

    # 测试 4: run_non_interactive_mode 流程
    print("\n--- 测试 4: run_non_interactive_mode 流程 ---")
    try:
        from startup.bootstrap.state import reset_state_for_tests
        reset_state_for_tests()

        captured = io.StringIO()
        with redirect_stdout(captured):
            asyncio.run(run_non_interactive_mode(
                prompt="test prompt",
                args={"cwd": os.getcwd()},
            ))

        output = captured.getvalue()
        assert "test prompt" in output, f"输出应包含 prompt, got: {output}"
        print(f"  输出: {output.strip()}")
        print("  [PASS] run_non_interactive_mode 流程")
    except Exception as e:
        print(f"  [FAIL] {e}")

    # 测试 5: show_setup_screens 首次使用
    print("\n--- 测试 5: show_setup_screens 首次使用 ---")
    try:
        from startup.bootstrap.state import reset_state_for_tests
        reset_state_for_tests()
        init()

        provider = AppStateProvider()
        # 捕获欢迎信息
        captured = io.StringIO()
        with redirect_stdout(captured):
            onboarding_shown = asyncio.run(show_setup_screens(provider))

        output = captured.getvalue()
        if onboarding_shown:
            assert "欢迎使用" in output, f"应显示欢迎信息, got: {output}"
            print(f"  onboarding_shown=True, 输出包含欢迎信息")
        else:
            print(f"  onboarding_shown=False（已完成 onboarding）")
        print("  [PASS] show_setup_screens 首次使用")
    except Exception as e:
        print(f"  [FAIL] {e}")

    # 测试 6: _set_model / _set_verbose 辅助函数
    print("\n--- 测试 6: _set_model / _set_verbose 辅助函数 ---")
    try:
        from startup.state.app_state import AppState
        state = AppState()
        assert state.model is None
        assert state.verbose is False

        state = _set_model(state, "claude-3.5-sonnet")
        assert state.model == "claude-3.5-sonnet"

        state = _set_verbose(state, True)
        assert state.verbose is True

        print(f"  model={state.model}, verbose={state.verbose}")
        print("  [PASS] _set_model / _set_verbose 辅助函数")
    except Exception as e:
        print(f"  [FAIL] {e}")

    print("\n" + "=" * 60)
    print("所有测试完成")
    print("=" * 60)
