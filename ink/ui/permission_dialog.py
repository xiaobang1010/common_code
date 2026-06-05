"""权限确认对话框。

参考原始 TypeScript 实现: src/components/permissions/PermissionRequest.tsx

提供工具权限确认和 auto 模式首次确认对话框。
"""

from __future__ import annotations

import enum
import json
from dataclasses import dataclass
from typing import Optional


# ---------------------------------------------------------------------------
# PermissionDecision — 权限决定
# ---------------------------------------------------------------------------

class PermissionDecision(enum.Enum):
    """权限决定。"""
    ALLOW = "allow"
    DENY = "deny"
    ALWAYS_ALLOW = "always_allow"


# ---------------------------------------------------------------------------
# 权限对话框渲染辅助
# ---------------------------------------------------------------------------

def _fg(color: str, text: str) -> str:
    """为文本添加前景色。"""
    codes: dict[str, str] = {
        "red": "31", "green": "32", "yellow": "33",
        "blue": "34", "cyan": "36", "white": "37",
        "gray": "90", "grey": "90",
    }
    code = codes.get(color, "39")
    return f"\x1b[{code}m{text}\x1b[39m"


def _bold(text: str) -> str:
    """为文本添加粗体效果。"""
    return f"\x1b[1m{text}\x1b[22m"


def _dim(text: str) -> str:
    """为文本添加暗淡效果。"""
    return f"\x1b[2m{text}\x1b[22m"


def _truncate(text: str, max_length: int = 200) -> str:
    """截断文本。"""
    if len(text) <= max_length:
        return text
    return text[:max_length - 3] + "..."


# ---------------------------------------------------------------------------
# show_permission_dialog — 权限确认对话框
# ---------------------------------------------------------------------------

async def show_permission_dialog(
    tool_name: str,
    tool_input: dict,
    reason: str = "",
) -> PermissionDecision:
    """显示权限确认对话框。

    显示工具名和输入预览，提供选项：Allow / Deny / Always Allow。

    Args:
        tool_name: 工具名称
        tool_input: 工具输入参数
        reason: 请求权限的原因

    Returns:
        用户选择的权限决定
    """
    # 渲染对话框头部
    print()
    print(_fg("yellow", "━" * 50))
    print(_bold(_fg("yellow", "  ⚠ Permission Required")))

    # 工具名称
    print(f"  {_fg('cyan', 'Tool:')} {tool_name}")

    # 原因
    if reason:
        print(f"  {_fg('gray', 'Reason:')} {reason}")

    # 输入预览
    if tool_input:
        try:
            input_preview = json.dumps(tool_input, ensure_ascii=False, indent=2)
        except (TypeError, ValueError):
            input_preview = str(tool_input)

        # 截断过长的预览
        input_preview = _truncate(input_preview, max_length=300)
        print(f"  {_fg('gray', 'Input:')}")
        for line in input_preview.split("\n"):
            print(f"    {_dim(line)}")

    print(_fg("yellow", "━" * 50))

    # 选项
    print(f"  {_fg('green', '[A]')}llow  {_fg('red', '[D]')}eny  {_fg('cyan', '[E]')}ver allow")

    # 读取用户输入
    while True:
        try:
            choice = input("  Choice: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return PermissionDecision.DENY

        if choice in ("a", "allow", "y", "yes"):
            return PermissionDecision.ALLOW
        elif choice in ("d", "deny", "n", "no"):
            return PermissionDecision.DENY
        elif choice in ("e", "always", "ever"):
            return PermissionDecision.ALWAYS_ALLOW
        else:
            print(f"  {_fg('red', 'Invalid choice. Please enter A, D, or E.')}")


# ---------------------------------------------------------------------------
# show_auto_mode_opt_in — auto 模式首次确认
# ---------------------------------------------------------------------------

_AUTO_MODE_DESCRIPTION = (
    "Auto mode allows the assistant to execute tools without asking for "
    "permission each time. This is convenient but may result in unintended "
    "changes to your files or system."
)


async def show_auto_mode_opt_in() -> bool:
    """显示 auto 模式首次确认对话框。

    Returns:
        True 如果用户同意启用 auto 模式，False 否之
    """
    print()
    print(_fg("cyan", "━" * 50))
    print(_bold(_fg("cyan", "  Auto Mode")))

    # 描述
    for line in _AUTO_MODE_DESCRIPTION.split("\n"):
        print(f"  {line}")

    print()
    print(f"  {_fg('yellow', 'Do you want to enable auto mode?')}")
    print(f"  {_fg('green', '[Y]')}es  {_fg('red', '[N]')}o")
    print(_fg("cyan", "━" * 50))

    while True:
        try:
            choice = input("  Choice: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return False

        if choice in ("y", "yes"):
            return True
        elif choice in ("n", "no"):
            return False
        else:
            print(f"  {_fg('red', 'Invalid choice. Please enter Y or N.')}")


# ---------------------------------------------------------------------------
# 测试块
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=== permission_dialog.py 测试 ===\n")

    # 1. 测试 PermissionDecision 枚举
    print("--- PermissionDecision ---")
    for decision in PermissionDecision:
        print(f"  {decision.name}: {decision.value}")

    # 2. 测试对话框渲染（非交互式，仅展示格式）
    print("\n--- 权限对话框格式预览 ---")
    # 模拟对话框输出（不等待用户输入）
    print()
    print(_fg("yellow", "━" * 50))
    print(_bold(_fg("yellow", "  ⚠ Permission Required")))
    print(f"  {_fg('cyan', 'Tool:')} write_file")
    print(f"  {_fg('gray', 'Reason:')} Writing to project source file")
    print(f"  {_fg('gray', 'Input:')}")
    print(f"    {_dim('{')}")
    print(f"    {_dim('  \"path\": \"/src/main.py\",')}")
    print(f"    {_dim('  \"content\": \"print(\\\"hello\\\")\"')}")
    print(f"    {_dim('}')}")
    print(_fg("yellow", "━" * 50))
    print(f"  {_fg('green', '[A]')}llow  {_fg('red', '[D]')}eny  {_fg('cyan', '[E]')}ver allow")

    # 3. 测试 auto 模式对话框格式预览
    print("\n--- Auto 模式对话框格式预览 ---")
    print()
    print(_fg("cyan", "━" * 50))
    print(_bold(_fg("cyan", "  Auto Mode")))
    for line in _AUTO_MODE_DESCRIPTION.split("\n"):
        print(f"  {line}")
    print()
    print(f"  {_fg('yellow', 'Do you want to enable auto mode?')}")
    print(f"  {_fg('green', '[Y]')}es  {_fg('red', '[N]')}o")
    print(_fg("cyan", "━" * 50))

    # 4. 测试 _truncate
    print("\n--- _truncate ---")
    short = "hello"
    long = "x" * 300
    print(f"  短文本: {_truncate(short)}")
    print(f"  长文本: {_truncate(long)[:50]}... (长度: {len(_truncate(long))})")

    print("\n=== 测试完成 ===")
