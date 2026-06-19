"""工具协议定义 — 参考原始 Tool.ts 的胖接口设计。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# ToolResult — 工具执行结果
# ---------------------------------------------------------------------------

@dataclass
class ToolResult:
    """工具执行结果。"""

    content: str
    is_error: bool = False
    metadata: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# ToolUseContext — 工具执行上下文
# ---------------------------------------------------------------------------

@dataclass
class ToolUseContext:
    """工具执行上下文。"""

    permission_decision: str | None = None
    messages: list = field(default_factory=list)
    file_state_cache: dict = field(default_factory=dict)
    abort_controller: Any = None
    tool_use_id: str = ""


# ---------------------------------------------------------------------------
# Tool — 工具协议定义（胖接口）
# ---------------------------------------------------------------------------

@dataclass
class Tool:
    """工具协议定义，参考原始 Tool.ts 的胖接口设计。"""

    # --- 必填字段 ---
    name: str
    description: str
    input_schema: type[BaseModel]
    execute: Callable  # async def execute(input, context) -> ToolResult
    prompt: str

    # --- 可选渲染 / 验证 / 权限回调 ---
    render: Callable | None = None
    validate_input: Callable | None = None
    get_tool_permission: Callable | None = None
    render_tool_use: Callable | None = None
    render_tool_result: Callable | None = None
    destructure_function: Callable | None = None

    # --- 行为标志 ---
    user_visible: bool = True
    is_concurrent: bool = False
    is_read_only: bool = False
    requires_permission: bool = True
    can_be_replaced_by_mcp: bool = False

    # --- 别名 ---
    aliases: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 工厂函数
# ---------------------------------------------------------------------------

async def _default_execute(_input: Any, _context: ToolUseContext) -> ToolResult:
    """execute 的默认实现 — 返回未实现错误。"""
    return ToolResult(content="Tool execute not implemented", is_error=True)


def _default_render(_input: Any, _context: ToolUseContext) -> str:
    """render 的默认实现 — 返回空字符串。"""
    return ""


def build_tool(**kwargs: Any) -> Tool:
    """工厂函数，提供 execute/render 的默认实现。

    用法：
        tool = build_tool(
            name="Bash",
            description="执行 shell 命令",
            input_schema=BashInput,
            execute=my_execute_fn,
            prompt="运行 bash 命令",
        )
    """
    kwargs.setdefault("execute", _default_execute)
    kwargs.setdefault("render", _default_render)
    kwargs.setdefault("aliases", [])
    return Tool(**kwargs)


# ---------------------------------------------------------------------------
# 工具名匹配
# ---------------------------------------------------------------------------

def tool_matches_name(tool: Tool, name: str) -> bool:
    """检查工具名或别名是否匹配给定名称。"""
    return tool.name == name or name in tool.aliases
