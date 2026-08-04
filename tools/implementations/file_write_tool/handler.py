"""Write 工具执行逻辑 — 返回结构化结果。"""

from __future__ import annotations

from tools.implementations.file_write_tool.schema import FileWriteInput
from tools.implementations.runtime.paths import resolve_workspace_path
from tools.protocol import ToolUseContext


async def handle_write(inp: FileWriteInput, context: ToolUseContext) -> dict:
    """创建目录（如需要）→ 写入文件。

    Returns:
        结构化结果字典：
        {
            "file_path": 绝对路径,
            "action": "created" / "overwritten",
            "bytes_written": 写入字节数,
        }

    Raises:
        ToolExecutionError: 路径越出工作区边界
    """
    # 路径沙箱：解析并校验工作区边界
    file_path = resolve_workspace_path(inp.file_path)

    # 覆盖前检测：区分创建 / 覆盖两种动作
    existed = file_path.exists()

    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(inp.content, encoding="utf-8")

    return {
        "file_path": str(file_path),
        "action": "overwritten" if existed else "created",
        "bytes_written": len(inp.content.encode("utf-8")),
    }


def format_model_content(structured: dict) -> str:
    """结构化结果 → 给模型的文本。"""
    action = "已覆盖" if structured.get("action") == "overwritten" else "已创建"
    return f"文件 {structured['file_path']} {action}成功。"
