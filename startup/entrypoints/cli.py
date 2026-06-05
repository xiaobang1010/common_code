"""CLI 入口，参考原始 cli.tsx 的设计。

快速路径分发：
  - --version: 零 import 直接输出版本号并退出
  - mcp serve: 动态加载 MCP 服务器模块
  - 交互模式: 动态加载 main 模块启动 REPL
"""

from __future__ import annotations

import argparse
import asyncio
import sys


# ---------------------------------------------------------------------------
# --version 快速路径：在 import 任何重模块之前处理
# ---------------------------------------------------------------------------

def _handle_version_fast_path() -> bool:
    """检查 --version 快速路径，零重模块 import。

    Returns:
        True 如果已处理（应退出），False 否则。
    """
    args = sys.argv[1:]
    if len(args) == 1 and args[0] in ("--version", "-v", "-V"):
        from startup.constants.version import VERSION, PRODUCT_NAME
        print(f"{VERSION} ({PRODUCT_NAME})")
        return True
    return False


# ---------------------------------------------------------------------------
# argparse 定义
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    """构建 CLI 参数解析器。

    注意：不使用 argparse subparsers，因为与位置参数 prompt 冲突。
    MCP 子命令通过手动检查 sys.argv 实现。
    """
    parser = argparse.ArgumentParser(
        prog="common-code-py",
        description="Common Code Python Edition",
    )

    parser.add_argument(
        "--version",
        action="store_true",
        help="输出版本号并退出",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="详细模式",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="指定模型",
    )
    parser.add_argument(
        "--print",
        action="store_true",
        dest="print_mode",
        help="非交互模式，直接输出结果",
    )
    parser.add_argument(
        "--output-format",
        type=str,
        choices=["text", "json", "stream-json"],
        default="text",
        help="输出格式（text/json/stream-json）",
    )
    parser.add_argument(
        "--allowedTools",
        nargs="*",
        default=None,
        help="允许的工具列表",
    )
    parser.add_argument(
        "--disallowedTools",
        nargs="*",
        default=None,
        help="禁止的工具列表",
    )

    # 位置参数：用户提示
    parser.add_argument(
        "prompt",
        nargs="*",
        default=None,
        help="用户输入的提示",
    )

    return parser


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def main() -> None:
    """CLI 主入口。"""
    # 快速路径：--version 在 import 任何重模块之前处理
    if _handle_version_fast_path():
        return

    # 快速路径：mcp serve — 手动检测，避免 argparse 子命令冲突
    argv = sys.argv[1:]
    if len(argv) >= 2 and argv[0] == "mcp" and argv[1] == "serve":
        from startup.entrypoints.init import init
        init()
        # 动态加载 MCP 服务器模块（尚未实现，预留接口）
        try:
            from query.services.mcp import run_mcp_server
            run_mcp_server()
        except ImportError:
            print("Error: MCP server module not yet implemented", file=sys.stderr)
            sys.exit(1)
        return

    parser = _build_parser()
    args = parser.parse_args()

    # --version 通过 argparse 也支持（非快速路径）
    if args.version:
        from startup.constants.version import VERSION, PRODUCT_NAME
        print(f"{VERSION} ({PRODUCT_NAME})")
        return

    # 交互 / 非交互模式：初始化 + 动态加载 main 模块
    from startup.entrypoints.init import init
    init()

    # 将 CLI 参数写入 bootstrap state
    from startup.bootstrap.state import set_verbose, set_model
    if args.verbose:
        set_verbose(True)
    if args.model:
        set_model(args.model)

    # 动态加载主循环
    prompt_text = " ".join(args.prompt) if args.prompt else None
    try:
        from main import run_interactive_mode, run_non_interactive_mode

        # 构建 CLI 参数字典，传递给 main 模块
        main_args: dict = {
            "cwd": None,
            "permission_mode": "default",
            "model": args.model,
            "verbose": args.verbose,
            "output_format": args.output_format,
            "allowed_tools": args.allowedTools,
            "disallowed_tools": args.disallowedTools,
        }

        if args.print_mode and prompt_text:
            # 非交互模式
            asyncio.run(run_non_interactive_mode(prompt_text, main_args))
        else:
            # 交互模式
            asyncio.run(run_interactive_mode(main_args))
    except ImportError as e:
        if prompt_text:
            print(f"Prompt: {prompt_text}")
        else:
            print(f"Interactive REPL not yet available: {e}")


# ---------------------------------------------------------------------------
# 测试块
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 60)
    print("cli.py 测试")
    print("=" * 60)

    # 测试 1: --version 快速路径
    print("\n--- 测试 1: --version 快速路径 ---")
    try:
        sys.argv = ["cli.py", "--version"]
        # _handle_version_fast_path 应返回 True 并打印版本
        import io
        from contextlib import redirect_stdout

        captured = io.StringIO()
        with redirect_stdout(captured):
            handled = _handle_version_fast_path()
        output = captured.getvalue().strip()
        assert handled, "--version 应被快速路径处理"
        assert "0.1.0" in output, f"版本号应包含 0.1.0, got: {output}"
        assert "common-code-py" in output, f"应包含产品名, got: {output}"
        print(f"  输出: {output}")
        print("  [PASS] --version 快速路径")
    except Exception as e:
        print(f"  [FAIL] {e}")

    # 测试 2: -v 快速路径
    print("\n--- 测试 2: -v 快速路径 ---")
    try:
        sys.argv = ["cli.py", "-v"]
        captured = io.StringIO()
        with redirect_stdout(captured):
            handled = _handle_version_fast_path()
        output = captured.getvalue().strip()
        assert handled, "-v 应被快速路径处理"
        assert "0.1.0" in output
        print(f"  输出: {output}")
        print("  [PASS] -v 快速路径")
    except Exception as e:
        print(f"  [FAIL] {e}")

    # 测试 3: 非 --version 参数不触发快速路径
    print("\n--- 测试 3: 非 --version 不触发快速路径 ---")
    try:
        sys.argv = ["cli.py", "--verbose"]
        handled = _handle_version_fast_path()
        assert not handled, "--verbose 不应触发快速路径"
        print("  [PASS] 非 --version 不触发快速路径")
    except Exception as e:
        print(f"  [FAIL] {e}")

    # 测试 4: argparse 解析基本参数
    print("\n--- 测试 4: argparse 解析 ---")
    try:
        parser = _build_parser()
        sys.argv = ["cli.py", "--verbose", "--model", "gpt-4o", "hello", "world"]
        args = parser.parse_args()
        assert args.verbose is True
        assert args.model == "gpt-4o"
        assert args.prompt == ["hello", "world"]
        assert args.output_format == "text"
        print(f"  verbose={args.verbose}, model={args.model}, prompt={args.prompt}")
        print("  [PASS] argparse 解析")
    except Exception as e:
        print(f"  [FAIL] {e}")

    # 测试 5: argparse --output-format
    print("\n--- 测试 5: --output-format ---")
    try:
        parser = _build_parser()
        sys.argv = ["cli.py", "--output-format", "json"]
        args = parser.parse_args()
        assert args.output_format == "json"
        print(f"  output_format={args.output_format}")
        print("  [PASS] --output-format")
    except Exception as e:
        print(f"  [FAIL] {e}")

    # 测试 6: argparse --print 模式
    print("\n--- 测试 6: --print 模式 ---")
    try:
        parser = _build_parser()
        sys.argv = ["cli.py", "--print", "test", "prompt"]
        args = parser.parse_args()
        assert args.print_mode is True
        assert args.prompt == ["test", "prompt"]
        print(f"  print_mode={args.print_mode}, prompt={args.prompt}")
        print("  [PASS] --print 模式")
    except Exception as e:
        print(f"  [FAIL] {e}")

    # 测试 7: init() memoize 行为
    print("\n--- 测试 7: init() memoize 行为 ---")
    try:
        from startup.entrypoints.init import init, is_initialized, reset_init_for_tests
        reset_init_for_tests()
        assert not is_initialized()
        init()
        assert is_initialized()
        init()  # 第二次调用应无副作用
        assert is_initialized()
        print("  [PASS] init() memoize 行为")
    except Exception as e:
        print(f"  [FAIL] {e}")

    print("\n" + "=" * 60)
    print("所有测试完成")
    print("=" * 60)
