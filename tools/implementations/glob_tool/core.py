"""GlobTool — 文件模式匹配。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel

from tools.protocol import Tool, ToolResult, ToolUseContext, build_tool


# ---------------------------------------------------------------------------
# 输入模型
# ---------------------------------------------------------------------------

class GlobInput(BaseModel):
    """Glob 工具输入。"""

    pattern: str
    path: str | None = None


# ---------------------------------------------------------------------------
# 工具描述
# ---------------------------------------------------------------------------

GLOB_PROMPT = """\
快速文件模式匹配工具，适用于任何代码库大小。

使用说明：
- 支持 glob 模式，如 "**/*.js" 或 "src/**/*.ts"
- 返回按修改时间排序的匹配文件路径
- 当需要按名称模式查找文件时使用此工具
"""


# ---------------------------------------------------------------------------
# execute
# ---------------------------------------------------------------------------

async def _execute(inp: GlobInput, _context: ToolUseContext) -> ToolResult:
    """使用 pathlib.Path.glob 匹配文件。"""
    search_path = Path(inp.path) if inp.path else Path.cwd()

    if not search_path.exists():
        return ToolResult(
            content=f"目录不存在：{inp.path}",
            is_error=True,
        )

    if not search_path.is_dir():
        return ToolResult(
            content=f"路径不是目录：{inp.path}",
            is_error=True,
        )

    try:
        matches = sorted(
            search_path.glob(inp.pattern),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    except Exception as exc:
        return ToolResult(
            content=f"Glob 匹配失败：{exc}",
            is_error=True,
        )

    # 只保留文件（排除目录）
    files = [p for p in matches if p.is_file()]

    if not files:
        return ToolResult(content="未找到匹配文件", is_error=False)

    # 转为相对路径（如果可能）
    try:
        rel_paths = [str(f.relative_to(search_path)) for f in files]
    except ValueError:
        rel_paths = [str(f) for f in files]

    output = "\n".join(rel_paths)
    return ToolResult(content=output, is_error=False)


# ---------------------------------------------------------------------------
# 工厂函数
# ---------------------------------------------------------------------------

def get_glob_tool() -> Tool:
    """返回 GlobTool 实例。"""
    return build_tool(
        name="Glob",
        description="文件模式匹配",
        input_schema=GlobInput,
        execute=_execute,
        prompt=GLOB_PROMPT,
        is_read_only=True,
    )
