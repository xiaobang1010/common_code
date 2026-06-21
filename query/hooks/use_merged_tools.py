"""动态工具池组装 — 合并内置工具和 MCP 工具。
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, create_model

from query.services.mcp.client import MCPClient
from query.services.mcp.types import MCPTool
from tools.protocol import Tool, ToolResult, ToolUseContext, build_tool

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# JSON Schema → Pydantic Model 转换
# ---------------------------------------------------------------------------

def _json_schema_type_to_python(schema: dict) -> type:
    """将 JSON Schema 类型映射为 Python 类型。"""
    type_str = schema.get("type", "string")

    if type_str == "string":
        return str
    elif type_str == "integer":
        return int
    elif type_str == "number":
        return float
    elif type_str == "boolean":
        return bool
    elif type_str == "array":
        item_schema = schema.get("items", {})
        item_type = _json_schema_type_to_python(item_schema)
        return list[item_type]  # type: ignore[valid-type]
    elif type_str == "object":
        return dict
    else:
        return Any


def json_schema_to_pydantic(schema: dict, model_name: str = "DynamicInput") -> type[BaseModel]:
    """将 JSON Schema 转换为 Pydantic Model。

    仅处理顶层 type=object 的 schema，提取 properties 作为字段。
    """
    if schema.get("type") != "object":
        # 非 object 类型，包装为单字段 model
        return create_model(model_name, input=(Any, ...))

    properties = schema.get("properties", {})
    required_fields = set(schema.get("required", []))

    field_definitions: dict[str, Any] = {}
    for prop_name, prop_schema in properties.items():
        if not isinstance(prop_schema, dict):
            field_definitions[prop_name] = (Any, None)
            continue

        python_type = _json_schema_type_to_python(prop_schema)
        default = prop_schema.get("default")

        if prop_name in required_fields:
            if default is not None:
                field_definitions[prop_name] = (python_type, default)
            else:
                field_definitions[prop_name] = (python_type, ...)
        else:
            field_definitions[prop_name] = (python_type, None)

    return create_model(model_name, **field_definitions)


# ---------------------------------------------------------------------------
# MCPTool → Tool 转换
# ---------------------------------------------------------------------------

def mcp_tool_to_tool(mcp_tool: MCPTool, client: MCPClient) -> Tool:
    """将 MCPTool 转换为 Tool 对象。

    - execute 函数调用 client.call_tool
    - input_schema 从 JSON Schema 转换为 Pydantic Model
    """
    # 转换 input_schema 为 Pydantic Model
    model_name = f"{client.name}_{mcp_tool.name}_Input"
    input_schema = json_schema_to_pydantic(mcp_tool.input_schema, model_name)

    # 构建 execute 函数
    async def execute(input_model: BaseModel, context: ToolUseContext) -> ToolResult:
        arguments = input_model.model_dump()
        try:
            result = await client.call_tool(mcp_tool.name, arguments)
            return ToolResult(content=result)
        except Exception as e:
            return ToolResult(content=str(e), is_error=True)

    # 构建完整工具名
    tool_name = f"mcp__{client.name}__{mcp_tool.name}"

    return build_tool(
        name=tool_name,
        description=mcp_tool.description,
        input_schema=input_schema,
        execute=execute,
        prompt=mcp_tool.description,
        can_be_replaced_by_mcp=False,
    )


# ---------------------------------------------------------------------------
# 工具池合并
# ---------------------------------------------------------------------------

async def get_merged_tools(
    builtin_tools: list[Tool],
    mcp_clients: dict[str, MCPClient],
    deny_rules: list | None = None,
) -> list[Tool]:
    """合并内置工具和 MCP 工具。

    步骤：
    1. 收集所有 MCP 工具
    2. 转换 MCPTool 为 Tool 对象
    3. 去重（同名工具优先使用内置）
    4. 应用 deny 规则过滤
    5. 返回合并后的工具列表
    """
    merged: list[Tool] = list(builtin_tools)
    seen_names: set[str] = {tool.name for tool in builtin_tools}

    # 1. 收集所有 MCP 工具并转换
    for server_name, client in mcp_clients.items():
        if client.state.value != "connected":
            continue

        try:
            mcp_tools = await client.list_tools()
            for mcp_tool in mcp_tools:
                tool = mcp_tool_to_tool(mcp_tool, client)

                # 3. 去重：同名工具优先使用内置
                if tool.name in seen_names:
                    logger.debug(
                        "Skipping MCP tool '%s' (name conflict with builtin)", tool.name
                    )
                    continue

                seen_names.add(tool.name)
                merged.append(tool)

        except Exception:
            logger.exception("Failed to list tools from MCP server '%s'", server_name)

    # 4. 应用 deny 规则过滤
    if deny_rules:
        merged = [
            tool for tool in merged
            if not _is_tool_denied(tool, deny_rules)
        ]

    return merged


def _is_tool_denied(tool: Tool, deny_rules: list) -> bool:
    """检查工具是否被 deny 规则匹配。

    deny_rules 中的每项可以是：
    - str：工具名精确匹配
    - dict：含 tool_pattern 字段的规则
    """
    for rule in deny_rules:
        if isinstance(rule, str):
            if tool.name == rule:
                return True
        elif isinstance(rule, dict):
            pattern = rule.get("tool_pattern", "")
            if pattern and (tool.name == pattern or tool.name.startswith(pattern)):
                return True
    return False
