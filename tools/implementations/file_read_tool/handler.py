"""Read 工具执行逻辑 — 返回结构化结果。"""

from __future__ import annotations

from tools.implementations.file_read_tool.schema import FileReadInput
from tools.implementations.runtime.errors import (
    file_not_found_error,
    not_a_file_error,
)
from tools.implementations.runtime.paths import resolve_workspace_path
from tools.protocol import ToolUseContext

# 不带 offset/limit 时默认读取的最大行数，避免整文件灌进上下文
DEFAULT_READ_LINES = 2000


async def handle_read(inp: FileReadInput, context: ToolUseContext) -> dict:
    """读取文件内容，支持按行号范围分段读取。

    Returns:
        结构化结果字典：
        {
            "file_path": 绝对路径,
            "content": cat -n 格式的带行号文本（可能带分段提示）,
            "start_line": 起始行号, "end_line": 结束行号,
            "total_lines": 文件总行数,
            "mtime": 整数秒, "size": 字节数,
        }

    Raises:
        ToolExecutionError: 路径越界 / 文件不存在 / 不是文件
    """
    # 路径沙箱：解析并校验工作区边界
    file_path = resolve_workspace_path(inp.file_path)

    if not file_path.exists():
        raise file_not_found_error(inp.file_path)
    if not file_path.is_file():
        raise not_a_file_error(inp.file_path)

    st = file_path.stat()

    # 分段读取：offset 为 1 起始行号；不带 limit 时默认只读前 DEFAULT_READ_LINES 行
    offset = max(1, inp.offset if inp.offset is not None else 1)
    has_explicit_limit = inp.limit is not None
    limit = inp.limit if has_explicit_limit else DEFAULT_READ_LINES
    end_line = offset + limit - 1

    # 按行流式读取：只保留目标行区间，不整文件载入内存
    selected: list[str] = []
    total_lines = 0
    with file_path.open(encoding="utf-8", errors="replace") as f:
        for raw in f:
            total_lines += 1
            if offset <= total_lines <= end_line:
                selected.append(raw.rstrip("\r\n"))

    num_width = len(str(max(total_lines, 1)))
    numbered = [
        f"{i:>{num_width}}→{line}"
        for i, line in enumerate(selected, start=offset)
    ]
    content = "\n".join(numbered)

    # 默认读取被截断时提示分段读
    if not has_explicit_limit and total_lines > end_line:
        content += f"\n（文件共 {total_lines} 行，仅显示前 {end_line} 行，请用 offset/limit 分段读取）"

    return {
        "file_path": str(file_path),
        "content": content,
        "start_line": offset,
        "end_line": min(end_line, total_lines),
        "total_lines": total_lines,
        "mtime": int(st.st_mtime),
        "size": st.st_size,
    }


def format_model_content(structured: dict) -> str:
    """结构化结果 → 给模型的文本。

    一致性基线（mtime/size）置于开头，避免被结果预算按头部保留截断；
    供模型在后续 Write/Edit 里作为 base_mtime/base_size 回传。
    """
    mtime = structured.get("mtime")
    size = structured.get("size")
    header = ""
    if mtime is not None and size is not None:
        header = f"[文件基线] mtime={mtime} size={size}\n\n"
    return header + structured.get("content", "")
