"""GrepTool — 内容搜索。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from tools.protocol import Tool, ToolResult, ToolUseContext, build_tool


# ---------------------------------------------------------------------------
# 输入模型
# ---------------------------------------------------------------------------

class GrepInput(BaseModel):
    """Grep 工具输入。"""

    pattern: str
    path: str | None = None
    include: str | None = None
    output_mode: str = "content"


# ---------------------------------------------------------------------------
# 工具描述
# ---------------------------------------------------------------------------

GREP_PROMPT = """\
强大的内容搜索工具。

使用说明：
- 支持完整的正则表达式语法（如 "log.*Error", "function\\s+\\w+"）
- 使用 include 参数过滤文件（如 "*.js", "*.py"）
- 输出模式："content" 显示匹配行，"files_with_matches" 仅显示文件路径，"count" 显示匹配计数
- 默认输出模式为 "content"
"""


# ---------------------------------------------------------------------------
# _match_include
# ---------------------------------------------------------------------------

def _match_include(file_path: Path, include: str | None) -> bool:
    """检查文件是否匹配 include 模式。"""
    if include is None:
        return True
    # 支持 "*.py", "*.{ts,tsx}" 等模式
    name = file_path.name
    # 简单的通配符匹配
    if include.startswith("*."):
        ext = include[1:]  # 如 ".py"
        return name.endswith(ext)
    # 逗号分隔的多个模式
    if "," in include:
        patterns = [p.strip() for p in include.split(",")]
        return any(_match_include(file_path, p) for p in patterns)
    return True


# ---------------------------------------------------------------------------
# execute
# ---------------------------------------------------------------------------

async def _execute(inp: GrepInput, _context: ToolUseContext) -> ToolResult:
    """使用 re.search 搜索文件内容。"""
    search_path = Path(inp.path) if inp.path else Path.cwd()

    if not search_path.exists():
        return ToolResult(
            content=f"路径不存在：{inp.path}",
            is_error=True,
        )

    try:
        regex = re.compile(inp.pattern)
    except re.error as exc:
        return ToolResult(
            content=f"无效的正则表达式：{exc}",
            is_error=True,
        )

    # 收集要搜索的文件
    if search_path.is_file():
        files = [search_path]
    else:
        files = [f for f in search_path.rglob("*") if f.is_file()]

    # 过滤 include 模式
    if inp.include:
        files = [f for f in files if _match_include(f, inp.include)]

    # 排除 .git 等目录
    files = [f for f in files if ".git" not in f.parts]

    # 搜索
    matches_by_file: dict[str, list[tuple[int, str]]] = {}
    for f in files:
        try:
            content = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        file_matches: list[tuple[int, str]] = []
        for line_num, line in enumerate(content.splitlines(), start=1):
            if regex.search(line):
                file_matches.append((line_num, line))

        if file_matches:
            try:
                rel_path = str(f.relative_to(search_path))
            except ValueError:
                rel_path = str(f)
            matches_by_file[rel_path] = file_matches

    # 格式化输出
    if inp.output_mode == "files_with_matches":
        if not matches_by_file:
            return ToolResult(content="未找到匹配文件", is_error=False)
        output = "\n".join(matches_by_file.keys())
        return ToolResult(content=output, is_error=False)

    if inp.output_mode == "count":
        if not matches_by_file:
            return ToolResult(content="未找到匹配", is_error=False)
        lines: list[str] = []
        total = 0
        for filepath, file_matches in matches_by_file.items():
            count = len(file_matches)
            total += count
            lines.append(f"{filepath}:{count}")
        lines.append(f"\n共 {total} 处匹配，涉及 {len(matches_by_file)} 个文件")
        output = "\n".join(lines)
        return ToolResult(content=output, is_error=False)

    # content 模式（默认）
    if not matches_by_file:
        return ToolResult(content="未找到匹配", is_error=False)

    content_lines: list[str] = []
    for filepath, file_matches in matches_by_file.items():
        for line_num, line in file_matches:
            content_lines.append(f"{filepath}:{line_num}:{line}")

    output = "\n".join(content_lines)
    return ToolResult(content=output, is_error=False)


# ---------------------------------------------------------------------------
# 工厂函数
# ---------------------------------------------------------------------------

def get_grep_tool() -> Tool:
    """返回 GrepTool 实例。"""
    return build_tool(
        name="Grep",
        description="内容搜索",
        input_schema=GrepInput,
        execute=_execute,
        prompt=GREP_PROMPT,
        is_read_only=True,
    )
