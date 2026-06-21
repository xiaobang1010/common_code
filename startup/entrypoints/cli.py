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
