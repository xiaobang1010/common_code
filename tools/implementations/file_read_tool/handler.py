"""Read 工具执行逻辑 — 返回结构化结果。"""

from __future__ import annotations

from tools.implementations.file_read_tool.schema import FileReadInput
from tools.implementations.runtime.errors import (
    file_not_found_error,
    file_too_large_error,
    not_a_file_error,
)
from tools.implementations.runtime.paths import resolve_workspace_path
from tools.protocol import ToolUseContext

# 单文件大小上限（字节），超过要求分段读取
MAX_FILE_BYTES = 256 * 1024


async def handle_read(inp: FileReadInput, context: ToolUseContext) -> dict:
    """读取文件内容，支持行号范围。

    Returns:
        结构化结果字典：
        {
            "file_path": 绝对路径,
            "content": cat -n 格式的带行号文本,
            "start_line": 起始行号, "end_line": 结束行号,
            "total_lines": 文件总行数,
        }

    Raises:
        ToolExecutionError: 路径越界 / 文件不存在 / 不是文件 / 文件过大
    """
    # 路径沙箱：解析并校验工作区边界
    file_path = resolve_workspace_path(inp.file_path)

    if not file_path.exists():
        raise file_not_found_error(inp.file_path)
    if not file_path.is_file():
        raise not_a_file_error(inp.file_path)

    # 大小上限检查，避免大文件撑爆内存与上下文
    if file_path.stat().st_size > MAX_FILE_BYTES:
        raise file_too_large_error(inp.file_path, MAX_FILE_BYTES)

    content = file_path.read_text(encoding="utf-8", errors="replace")
    lines = content.splitlines()

    # 应用 offset 和 limit（offset 从 1 开始）
    offset = inp.offset if inp.offset is not None else 1
    limit = inp.limit if inp.limit is not None else len(lines)
    start = max(0, offset - 1)
    end = min(len(lines), start + limit)
    selected = lines[start:end]

    # cat -n 格式行号
    num_width = len(str(max(end, 1)))
    numbered = [
        f"{i:>{num_width}}→{line}"
        for i, line in enumerate(selected, start=start + 1)
    ]

    return {
        "file_path": str(file_path),
        "content": "\n".join(numbered),
        "start_line": start + 1,
        "end_line": end,
        "total_lines": len(lines),
    }


def format_model_content(structured: dict) -> str:
    """结构化结果 → 给模型的文本。"""
    return structured.get("content", "")
