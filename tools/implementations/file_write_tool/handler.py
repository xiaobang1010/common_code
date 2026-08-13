"""Write 工具执行逻辑 — 返回结构化结果。"""

from __future__ import annotations

from server.file_events import notify_file_changed
from server.paths import MAX_EDITABLE_BYTES
from tools.implementations.file_write_tool.schema import FileWriteInput
from tools.implementations.runtime.errors import (
    file_exists_requires_baseline_error,
    file_modified_error,
    file_too_large_error,
)
from tools.implementations.runtime.paths import resolve_workspace_path
from tools.protocol import ToolUseContext


async def handle_write(inp: FileWriteInput, context: ToolUseContext) -> dict:
    """创建目录（如需要）→ 写入文件（覆盖已存在文件需带一致性基线）。

    Returns:
        结构化结果字典：
        {
            "file_path": 绝对路径,
            "action": "created" / "overwritten",
            "bytes_written": 写入字节数,
        }

    Raises:
        ToolExecutionError: 路径越界 / 覆盖缺基线 / 基线不一致 / 文件过大
    """
    # 路径沙箱：解析并校验工作区边界
    file_path = resolve_workspace_path(inp.file_path)

    existed = file_path.exists()

    # 覆盖已存在文件：强制要求基线，防止用旧快照覆盖他人改动
    if existed:
        if inp.base_mtime is None or inp.base_size is None:
            raise file_exists_requires_baseline_error(inp.file_path)
        st = file_path.stat()
        if int(st.st_mtime) != inp.base_mtime or st.st_size != inp.base_size:
            raise file_modified_error(inp.file_path)

    # 大小上限护栏
    size = len(inp.content.encode("utf-8"))
    if size > MAX_EDITABLE_BYTES:
        raise file_too_large_error(inp.file_path, MAX_EDITABLE_BYTES)

    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(inp.content, encoding="utf-8")

    # 写盘成功后广播文件变更事件（供前端刷新文件树 / 标记过期）
    notify_file_changed(str(file_path), "write", int(file_path.stat().st_mtime), size)

    return {
        "file_path": str(file_path),
        "action": "overwritten" if existed else "created",
        "bytes_written": size,
    }


def format_model_content(structured: dict) -> str:
    """结构化结果 → 给模型的文本。"""
    action = "已覆盖" if structured.get("action") == "overwritten" else "已创建"
    return f"文件 {structured['file_path']} {action}成功。"
