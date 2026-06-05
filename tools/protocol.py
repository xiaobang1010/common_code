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


# ---------------------------------------------------------------------------
# 自测
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from tools.utils.schema import pydantic_to_openai_function_schema, tool_to_openai_schema

    # ---- 1. 测试 Tool dataclass 创建 ----
    class DummyInput(BaseModel):
        query: str
        limit: int = 10

    async def dummy_execute(inp: DummyInput, ctx: ToolUseContext) -> ToolResult:
        return ToolResult(content=f"query={inp.query}, limit={inp.limit}")

    tool = Tool(
        name="Dummy",
        description="A dummy tool for testing",
        input_schema=DummyInput,
        execute=dummy_execute,
        prompt="Use this dummy tool",
        aliases=["dummy_alias", "d"],
    )
    assert tool.name == "Dummy"
    assert tool.user_visible is True
    assert tool.is_concurrent is False
    assert tool.is_read_only is False
    assert tool.requires_permission is True
    assert tool.aliases == ["dummy_alias", "d"]
    print("[PASS] Tool dataclass 创建")

    # ---- 2. 测试 build_tool() 工厂 ----
    tool2 = build_tool(
        name="Factory",
        description="Built via factory",
        input_schema=DummyInput,
        prompt="Factory tool prompt",
    )
    assert tool2.name == "Factory"
    assert tool2.render is not None
    assert tool2.aliases == []
    print("[PASS] build_tool() 工厂")

    # ---- 3. 测试 tool_matches_name() 含别名匹配 ----
    assert tool_matches_name(tool, "Dummy") is True
    assert tool_matches_name(tool, "dummy_alias") is True
    assert tool_matches_name(tool, "d") is True
    assert tool_matches_name(tool, "nonexistent") is False
    assert tool_matches_name(tool2, "Factory") is True
    assert tool_matches_name(tool2, "other") is False
    print("[PASS] tool_matches_name() 含别名匹配")

    # ---- 4. 测试 pydantic_to_openai_function_schema() 转换 ----
    schema = pydantic_to_openai_function_schema(
        model=DummyInput,
        name="Dummy",
        description="A dummy tool for testing",
    )
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "Dummy"
    assert schema["function"]["description"] == "A dummy tool for testing"
    params = schema["function"]["parameters"]
    assert params["type"] == "object"
    assert "query" in params["properties"]
    assert "limit" in params["properties"]
    # title 应被移除
    assert "title" not in params
    assert "title" not in params["properties"]["query"]
    print("[PASS] pydantic_to_openai_function_schema() 转换")

    # ---- 5. 测试 tool_to_openai_schema() ----
    tool_schema = tool_to_openai_schema(tool)
    assert tool_schema["function"]["name"] == "Dummy"
    assert "query" in tool_schema["function"]["parameters"]["properties"]
    print("[PASS] tool_to_openai_schema() 转换")

    print("\nAll tests passed!")
