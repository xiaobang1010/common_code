"""虚拟行号系统 - 读取时行号网格和 Closet 指针精确定位。

Drawer 以原始格式存储（不插入行号），读取时通过 render_with_line_numbers() 动态添加。
Closet 指针 ->date:L55-L72 可精确定位到 Drawer 的特定行范围。
"""

from __future__ import annotations

import re

_CLOSET_POINTER_RE = re.compile(r"->?(\d{4}-\d{2}-\d{2}):L(\d+)-L(\d+)")


def render_with_line_numbers(text: str, start: int = 1) -> str:
    """给每行添加 [N] 前缀。

    如果行已以 [数字] 开头则原样保留，但计数器仍前进。

    Args:
        text: 原始文本
        start: 起始行号（1-based）

    Returns:
        带行号前缀的文本
    """
    lines = text.split("\n")
    result = []
    line_num = start
    for line in lines:
        # Check if line already starts with [number]
        if re.match(r"^\[\d+\]", line):
            result.append(line)
        else:
            result.append(f"[{line_num}] {line}")
        line_num += 1
    return "\n".join(result)


def extract_line_range(text: str, start_line: int, end_line: int) -> str:
    """提取指定行范围（1-based）。

    Args:
        text: 原始文本
        start_line: 起始行（1-based, inclusive）
        end_line: 结束行（1-based, inclusive）

    Returns:
        指定行范围的文本
    """
    lines = text.split("\n")
    # Convert to 0-based
    start_idx = max(0, start_line - 1)
    end_idx = min(len(lines), end_line)
    return "\n".join(lines[start_idx:end_idx])


def parse_closet_pointer(date_line: str) -> tuple[str, int, int] | None:
    """解析 Closet 指针。

    格式：YYYY-MM-DD:Lstart-Lend 或 ->YYYY-MM-DD:Lstart-Lend

    Args:
        date_line: Closet 指针字符串

    Returns:
        (date, start_line, end_line) 或 None（解析失败）
    """
    m = _CLOSET_POINTER_RE.search(date_line)
    if m:
        return (m.group(1), int(m.group(2)), int(m.group(3)))
    return None
