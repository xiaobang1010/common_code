"""FileWriteTool — 写入文件。"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from tools.protocol import Tool, ToolResult, ToolUseContext, build_tool


# ---------------------------------------------------------------------------
# 输入模型
# ---------------------------------------------------------------------------

class FileWriteInput(BaseModel):
    """文件写入工具输入。"""

    file_path: str
    content: str


# ---------------------------------------------------------------------------
# 工具描述
# ---------------------------------------------------------------------------

FILE_WRITE_PROMPT = """\
将文件写入本地文件系统。

使用说明：
- 此工具会覆盖指定路径上的现有文件
- 优先使用 Edit 工具修改现有文件——它只发送差异。仅使用此工具创建新文件或完全重写
- file_path 参数必须是绝对路径，不能是相对路径
"""


# ---------------------------------------------------------------------------
# execute
# ---------------------------------------------------------------------------

async def _execute(inp: FileWriteInput, _context: ToolUseContext) -> ToolResult:
    """创建目录（如需要）→ 写入文件。"""
    file_path = Path(inp.file_path)

    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(inp.content, encoding="utf-8")
    except Exception as exc:
        return ToolResult(
            content=f"写入文件失败：{exc}",
            is_error=True,
        )

    action = "已创建" if not file_path.exists() else "已更新"
    return ToolResult(
        content=f"文件 {inp.file_path} {action}成功。",
        is_error=False,
    )


# ---------------------------------------------------------------------------
# 工厂函数
# ---------------------------------------------------------------------------

def get_file_write_tool() -> Tool:
    """返回 FileWriteTool 实例。"""
    return build_tool(
        name="Write",
        description="写入文件",
        input_schema=FileWriteInput,
        execute=_execute,
        prompt=FILE_WRITE_PROMPT,
    )
