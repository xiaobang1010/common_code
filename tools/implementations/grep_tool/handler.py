"""Grep 工具执行逻辑 — 返回结构化结果。"""

from __future__ import annotations

import re
from pathlib import Path

from tools.implementations.grep_tool.schema import GrepInput
from tools.implementations.runtime.errors import (
    ToolExecutionError,
    not_a_directory_error,
)
from tools.implementations.runtime.paths import (
    get_workspace_root,
    resolve_workspace_path,
)
from tools.protocol import ToolUseContext

# 结果上限
MAX_MATCH_LINES = 500   # content 模式最大匹配行数
MAX_MATCH_FILES = 100  # 最大文件数

# 遍历时跳过的目录（版本控制/依赖/缓存）
_EXCLUDED_DIRS = {
    ".git", ".hg", ".svn",
    "node_modules", "__pycache__", ".venv", "venv",
    "dist", "build", ".idea", ".vscode",
}


def _match_include(file_name: str, include: str | None) -> bool:
    """检查文件名是否匹配 include 模式（支持 "*.py"、逗号分隔多模式）。"""
    if include is None:
        return True
    if "," in include:
        return any(_match_include(file_name, p.strip()) for p in include.split(","))
    if include.startswith("*."):
        return file_name.endswith(include[1:])
    return True


def _iter_candidate_files(search_root: Path) -> list[Path]:
    """遍历候选文件，排除受管目录。"""
    files: list[Path] = []
    if search_root.is_file():
        return [search_root]

    stack = [search_root]
    while stack:
        current = stack.pop()
        try:
            entries = list(current.iterdir())
        except (PermissionError, OSError):
            continue
        for entry in entries:
            if entry.is_dir():
                if entry.name not in _EXCLUDED_DIRS:
                    stack.append(entry)
            elif entry.is_file():
                files.append(entry)
    return files


async def handle_grep(inp: GrepInput, context: ToolUseContext) -> dict:
    """正则搜索文件内容，支持三种输出模式。

    Returns:
        结构化结果字典：
        {
            "mode": 输出模式,
            "content": 格式化后的文本,
            "num_matches": 匹配总数, "num_files": 文件数,
            "truncated": 是否因上限截断,
        }

    Raises:
        ToolExecutionError: 无效正则 / 搜索根不是目录 / 路径越界
    """
    # 路径沙箱：未指定 path 时用工作区根
    if inp.path:
        search_root = resolve_workspace_path(inp.path)
    else:
        search_root = get_workspace_root()

    if search_root.exists() and not search_root.is_file() and not search_root.is_dir():
        raise not_a_directory_error(inp.path or str(search_root))

    try:
        regex = re.compile(inp.pattern)
    except re.error as exc:
        raise ToolExecutionError("invalid_regex", f"无效的正则表达式：{exc}")

    files = _iter_candidate_files(search_root)
    if inp.include:
        files = [f for f in files if _match_include(f.name, inp.include)]

    workspace_root = get_workspace_root()

    # 逐文件搜索（达到上限即停止）
    matches_by_file: dict[str, list[tuple[int, str]]] = {}
    total_matches = 0
    truncated = False
    for f in files:
        try:
            content = f.read_text(encoding="utf-8", errors="replace")
        except (OSError, UnicodeDecodeError):
            continue

        file_matches = [
            (line_num, line)
            for line_num, line in enumerate(content.splitlines(), start=1)
            if regex.search(line)
        ]
        if not file_matches:
            continue

        rel = _to_display_path(f, workspace_root)
        matches_by_file[rel] = file_matches
        total_matches += len(file_matches)

        if (
            len(matches_by_file) >= MAX_MATCH_FILES
            or total_matches >= MAX_MATCH_LINES
        ):
            truncated = True
            break

    formatted = _format_output(inp.output_mode, matches_by_file, total_matches, truncated)
    return {
        "mode": inp.output_mode,
        "content": formatted,
        "num_matches": total_matches,
        "num_files": len(matches_by_file),
        "truncated": truncated,
    }


def _to_display_path(path: Path, workspace_root: Path) -> str:
    """转为相对工作区的展示路径，失败时回退绝对路径。"""
    try:
        return path.relative_to(workspace_root).as_posix()
    except ValueError:
        return str(path)


def _format_output(
    mode: str,
    matches_by_file: dict[str, list[tuple[int, str]]],
    total_matches: int,
    truncated: bool,
) -> str:
    """按输出模式格式化结果文本。"""
    if not matches_by_file:
        return "未找到匹配"

    suffix = "\n（结果已截断，请缩小搜索范围或使用 include 过滤。）" if truncated else ""

    if mode == "files_with_matches":
        return "\n".join(matches_by_file.keys()) + suffix

    if mode == "count":
        lines = [f"{fp}:{len(ms)}" for fp, ms in matches_by_file.items()]
        lines.append(f"\n共 {total_matches} 处匹配，涉及 {len(matches_by_file)} 个文件")
        return "\n".join(lines) + suffix

    # content 模式（默认），行级输出受总上限约束
    content_lines: list[str] = []
    for fp, file_matches in matches_by_file.items():
        for line_num, line in file_matches:
            content_lines.append(f"{fp}:{line_num}:{line}")
            if len(content_lines) >= MAX_MATCH_LINES:
                return "\n".join(content_lines) + suffix
    return "\n".join(content_lines) + suffix


def format_model_content(structured: dict) -> str:
    """结构化结果 → 给模型的文本。"""
    return structured.get("content", "")
