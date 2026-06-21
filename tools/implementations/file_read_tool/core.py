"""FileReadTool — 读取文件内容。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel

from tools.protocol import Tool, ToolResult, ToolUseContext, build_tool


# ---------------------------------------------------------------------------
# 输入模型
# ---------------------------------------------------------------------------

class FileReadInput(BaseModel):
    """文件读取工具输入。"""

    file_path: str
    offset: int | None = None
    limit: int | None = None


# ---------------------------------------------------------------------------
# 工具描述
# ---------------------------------------------------------------------------

FILE_READ_PROMPT = """\
读取本地文件系统中的文件内容。

使用说明：
- file_path 参数必须是绝对路径，不能是相对路径
- 默认从文件开头读取全部内容
- 可以指定 offset（起始行号）和 limit（读取行数）来读取大文件的特定部分
- 结果使用 cat -n 格式，行号从 1 开始
- 此工具只能读取文件，不能读取目录
"""


# ---------------------------------------------------------------------------
# execute
# ---------------------------------------------------------------------------

async def _execute(inp: FileReadInput, _context: ToolUseContext) -> ToolResult:
    """读取文件内容，支持行号范围。"""
    file_path = Path(inp.file_path)

    if not file_path.exists():
        return ToolResult(
            content=f"文件不存在：{inp.file_path}",
            is_error=True,
        )

    if not file_path.is_file():
        return ToolResult(
            content=f"路径不是文件：{inp.file_path}",
            is_error=True,
        )

    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return ToolResult(
            content=f"读取文件失败：{exc}",
            is_error=True,
        )

    lines = content.splitlines()

    # 应用 offset 和 limit
    offset = inp.offset if inp.offset is not None else 1
    limit = inp.limit if inp.limit is not None else len(lines)

    # offset 从 1 开始
    start = max(0, offset - 1)
    end = min(len(lines), start + limit)

    selected_lines = lines[start:end]

    # 添加行号（cat -n 格式）
    max_line_num = end
    num_width = len(str(max_line_num))
    numbered_lines: list[str] = []
    for i, line in enumerate(selected_lines, start=start + 1):
        numbered_lines.append(f"{i:>{num_width}}→{line}")

    output = "\n".join(numbered_lines)
    return ToolResult(content=output, is_error=False)


# ---------------------------------------------------------------------------
# 工厂函数
# ---------------------------------------------------------------------------

def get_file_read_tool() -> Tool:
    """返回 FileReadTool 实例。"""
    return build_tool(
        name="Read",
        description="读取文件内容",
        input_schema=FileReadInput,
        execute=_execute,
        prompt=FILE_READ_PROMPT,
        is_read_only=True,
    )
