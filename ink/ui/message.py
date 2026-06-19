"""消息渲染组件。

参考原始 TypeScript 实现: src/components/Message.tsx

提供多态消息渲染、工具调用摘要和工具结果预览功能。
"""

from __future__ import annotations

import json
import textwrap
from typing import Optional

from .messages import MessageData


# ---------------------------------------------------------------------------
# ANSI 颜色辅助
# ---------------------------------------------------------------------------

def _fg(color: str, text: str) -> str:
    """为文本添加前景色。"""
    codes: dict[str, str] = {
        "black": "30", "red": "31", "green": "32", "yellow": "33",
        "blue": "34", "magenta": "35", "cyan": "36", "white": "37",
        "bright_black": "90", "bright_red": "91", "bright_green": "92",
        "bright_yellow": "93", "bright_blue": "94", "bright_magenta": "95",
        "bright_cyan": "96", "bright_white": "97",
        "gray": "90", "grey": "90",
    }
    if color.startswith("#") and len(color) == 7:
        r = int(color[1:3], 16)
        g = int(color[3:5], 16)
        b = int(color[5:7], 16)
        return f"\x1b[38;2;{r};{g};{b}m{text}\x1b[39m"
    code = codes.get(color, "39")
    return f"\x1b[{code}m{text}\x1b[39m"


def _dim(text: str) -> str:
    """为文本添加暗淡效果。"""
    return f"\x1b[2m{text}\x1b[22m"


def _bold(text: str) -> str:
    """为文本添加粗体效果。"""
    return f"\x1b[1m{text}\x1b[22m"


# ---------------------------------------------------------------------------
# render_tool_call_summary — 渲染工具调用摘要
# ---------------------------------------------------------------------------

def render_tool_call_summary(tool_call: dict) -> str:
    """渲染工具调用摘要。

    格式：[Tool: name] args_preview...

    Args:
        tool_call: 工具调用字典，包含 name 和 input

    Returns:
        格式化的工具调用摘要字符串
    """
    name = tool_call.get("name", "unknown")
    input_data = tool_call.get("input", {})

    # 生成参数预览
    if isinstance(input_data, dict) and input_data:
        try:
            args_preview = json.dumps(input_data, ensure_ascii=False)
        except (TypeError, ValueError):
            args_preview = str(input_data)
        # 截断过长的预览
        if len(args_preview) > 80:
            args_preview = args_preview[:77] + "..."
    else:
        args_preview = ""

    if args_preview:
        return f"{_fg('cyan', f'[Tool: {name}]')} {_dim(args_preview)}"
    return _fg("cyan", f"[Tool: {name}]")


# ---------------------------------------------------------------------------
# render_tool_result_preview — 渲染工具结果预览
# ---------------------------------------------------------------------------

def render_tool_result_preview(content: str, max_length: int = 200) -> str:
    """渲染工具结果预览（截断长内容）。

    Args:
        content: 工具结果内容
        max_length: 最大显示长度

    Returns:
        截断后的预览字符串
    """
    if not content:
        return _dim("(empty result)")

    # 移除多余空白
    preview = content.strip()

    # 截断
    if len(preview) > max_length:
        preview = preview[:max_length - 3] + "..."

    # 多行内容只显示第一行 + 行数
    lines = preview.split("\n")
    if len(lines) > 3:
        first_lines = "\n".join(lines[:3])
        remaining = len(lines) - 3
        preview = f"{first_lines}\n{_dim(f'... ({remaining} more lines)')}"
    else:
        preview = "\n".join(lines)

    return preview


# ---------------------------------------------------------------------------
# render_message — 多态分发渲染
# ---------------------------------------------------------------------------

# 消息角色前缀符号
_ROLE_PREFIXES = {
    "system": ("◆", "gray"),
    "user": ("▸", "blue"),
    "assistant": ("●", "white"),
    "tool": ("◈", "green"),
}


def render_message(msg: MessageData, width: int = 80) -> str:
    """多态分发渲染消息。

    - system → 灰色文本
    - user → 蓝色前缀 + 内容
    - assistant → 白色内容 + 工具调用摘要
    - tool → 绿色前缀 + 结果预览（截断）

    Args:
        msg: 消息数据
        width: 终端宽度，用于文本换行

    Returns:
        渲染后的字符串
    """
    prefix_char, prefix_color = _ROLE_PREFIXES.get(msg.role, ("·", "gray"))
    prefix = _fg(prefix_color, prefix_char)

    if msg.role == "system":
        return _render_system_message(msg, prefix, width)
    elif msg.role == "user":
        return _render_user_message(msg, prefix, width)
    elif msg.role == "assistant":
        return _render_assistant_message(msg, prefix, width)
    elif msg.role == "tool":
        return _render_tool_message(msg, prefix, width)
    else:
        return _render_unknown_message(msg, prefix, width)


def _render_system_message(msg: MessageData, prefix: str, width: int) -> str:
    """渲染系统消息：灰色文本。"""
    if msg.is_compact_boundary:
        return _dim("─ ─ ─  compact boundary  ─ ─ ─")

    content_width = width - 4
    lines = textwrap.wrap(msg.content, width=content_width) if msg.content else [""]
    wrapped = "\n".join(lines)
    return f"{prefix} {_dim(wrapped)}"


def _render_user_message(msg: MessageData, prefix: str, width: int) -> str:
    """渲染用户消息：蓝色前缀 + 内容。"""
    content_width = width - 4
    lines = textwrap.wrap(msg.content, width=content_width) if msg.content else [""]
    wrapped = "\n".join(lines)
    return f"{prefix} {wrapped}"


def _render_assistant_message(msg: MessageData, prefix: str, width: int) -> str:
    """渲染助手消息：白色内容 + 工具调用摘要。"""
    parts: list[str] = []

    # 文本内容
    if msg.content:
        content_width = width - 4
        lines = textwrap.wrap(msg.content, width=content_width)
        wrapped = "\n".join(lines)
        parts.append(f"{prefix} {wrapped}")

    # 工具调用摘要
    if msg.tool_calls:
        for tc in msg.tool_calls:
            parts.append(f"  {render_tool_call_summary(tc)}")

    if not parts:
        parts.append(f"{prefix} {_dim('(empty response)')}")

    return "\n".join(parts)


def _render_tool_message(msg: MessageData, prefix: str, width: int) -> str:
    """渲染工具消息：绿色前缀 + 结果预览。"""
    preview = render_tool_result_preview(msg.content, max_length=width - 6)
    # 缩进多行预览
    lines = preview.split("\n")
    indented = "\n  ".join(lines)
    return f"{prefix} {indented}"


def _render_unknown_message(msg: MessageData, prefix: str, width: int) -> str:
    """渲染未知角色消息。"""
    return f"{prefix} {_dim(f'[{msg.role}]')} {msg.content}"
