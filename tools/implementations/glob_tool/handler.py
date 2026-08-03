"""Glob 工具执行逻辑 — 返回结构化结果。"""

from __future__ import annotations

from tools.implementations.glob_tool.schema import GlobInput
from tools.implementations.runtime.errors import not_a_directory_error
from tools.implementations.runtime.paths import (
    get_workspace_root,
    resolve_workspace_path,
)
from tools.protocol import ToolUseContext

# 结果数量上限，超过截断并提示
MAX_RESULTS = 100


async def handle_glob(inp: GlobInput, context: ToolUseContext) -> dict:
    """按 glob 模式匹配文件，按修改时间倒序。

    Returns:
        结构化结果字典：
        {
            "filenames": 相对工作区的路径列表,
            "num_files": 返回数量,
            "truncated": 是否因上限截断,
        }

    Raises:
        ToolExecutionError: 搜索根不是目录 / 路径越界
    """
    # 路径沙箱：未指定 path 时用工作区根
    if inp.path:
        search_root = resolve_workspace_path(inp.path)
    else:
        search_root = get_workspace_root()

    if not search_root.is_dir():
        raise not_a_directory_error(inp.path or str(search_root))

    matches = sorted(
        search_root.glob(inp.pattern),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    # 只保留文件（排除目录）
    files = [p for p in matches if p.is_file()]

    truncated = len(files) > MAX_RESULTS
    files = files[:MAX_RESULTS]

    workspace_root = get_workspace_root()
    filenames = [_to_display_path(f, workspace_root) for f in files]

    return {
        "filenames": filenames,
        "num_files": len(filenames),
        "truncated": truncated,
    }


def _to_display_path(path, workspace_root) -> str:
    """转为相对工作区的展示路径，失败时回退绝对路径。"""
    try:
        rel = path.relative_to(workspace_root)
        return rel.as_posix()
    except ValueError:
        return str(path)


def format_model_content(structured: dict) -> str:
    """结构化结果 → 给模型的文本。"""
    filenames = structured.get("filenames", [])
    if not filenames:
        return "未找到匹配文件"
    lines = list(filenames)
    if structured.get("truncated"):
        lines.append("（结果已截断，请使用更精确的模式。）")
    return "\n".join(lines)
