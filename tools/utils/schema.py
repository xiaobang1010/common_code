"""Pydantic → OpenAI function calling Schema 转换工具函数。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel

if TYPE_CHECKING:
    from tools.protocol import Tool


def _remove_title(schema: dict) -> dict:
    """递归移除 Pydantic 自动添加的 title 字段（OpenAI 不需要）。"""
    schema.pop("title", None)
    if "properties" in schema:
        for prop in schema["properties"].values():
            if isinstance(prop, dict):
                _remove_title(prop)
    if "items" in schema and isinstance(schema["items"], dict):
        _remove_title(schema["items"])
    if "anyOf" in schema:
        for sub in schema["anyOf"]:
            if isinstance(sub, dict):
                _remove_title(sub)
    if "allOf" in schema:
        for sub in schema["allOf"]:
            if isinstance(sub, dict):
                _remove_title(sub)
    if "$defs" in schema:
        for defn in schema["$defs"].values():
            if isinstance(defn, dict):
                _remove_title(defn)
    return schema


def pydantic_to_openai_function_schema(
    model: type[BaseModel],
    name: str,
    description: str,
) -> dict:
    """将 Pydantic Model 转换为 OpenAI function calling 的 JSON Schema 格式。

    输出格式：
        {"type": "function", "function": {"name": ..., "description": ..., "parameters": ...}}
    """
    schema = model.model_json_schema()
    _remove_title(schema)
    # 确保顶层 type 为 object
    schema.setdefault("type", "object")
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": schema,
        },
    }


def tool_to_openai_schema(tool: Tool) -> dict:
    """将 Tool 转换为 OpenAI function schema。"""
    return pydantic_to_openai_function_schema(
        model=tool.input_schema,
        name=tool.name,
        description=tool.description,
    )
