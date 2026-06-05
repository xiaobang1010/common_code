"""API 工具函数 — 参考原始 src/utils/api.ts。"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from tools.utils.schema import tool_to_openai_schema

if TYPE_CHECKING:
    from tools.protocol import Tool


# ---------------------------------------------------------------------------
# prepend_user_context
# ---------------------------------------------------------------------------

def prepend_user_context(messages: list[dict], context: str) -> list[dict]:
    """在消息列表前插入 userContext（CLAUDE.md + 日期）。

    将上下文包装为 system-reminder 格式的 user 消息，插入到消息列表最前。
    """
    if not context:
        return messages

    context_message = {
        "role": "user",
        "content": (
            "<system-reminder>\n"
            "As you answer the user's questions, you can use the following context:\n"
            f"{context}\n"
            "\n"
            "IMPORTANT: this context may or may not be relevant to your tasks. "
            "You should not respond to this context unless it is highly relevant to your task.\n"
            "</system-reminder>\n"
        ),
    }
    return [context_message, *messages]


# ---------------------------------------------------------------------------
# append_system_context
# ---------------------------------------------------------------------------

def append_system_context(messages: list[dict], context: str) -> list[dict]:
    """在消息列表后追加系统上下文。"""
    if not context:
        return messages
    return [*messages, {"role": "system", "content": context}]


# ---------------------------------------------------------------------------
# tool_to_api_schema
# ---------------------------------------------------------------------------

def tool_to_api_schema(tool: Tool) -> dict:
    """将 Tool 转换为 OpenAI function calling schema。

    委托给 utils/schema.py 的 tool_to_openai_schema。
    """
    return tool_to_openai_schema(tool)


# ---------------------------------------------------------------------------
# build_api_request
# ---------------------------------------------------------------------------

def build_api_request(
    messages: list[dict],
    system_prompt: list[dict],
    tools: list[Tool],
    model: str,
    *,
    stream: bool = True,
    max_tokens: int = 8192,
    temperature: float = 1.0,
    **kwargs,
) -> dict:
    """构建完整的 OpenAI API 请求体。

    Args:
        messages: user/assistant/tool 消息列表
        system_prompt: system 消息列表（来自 build_system_messages）
        tools: Tool 对象列表
        model: 模型名
        stream: 是否流式（默认 True）
        max_tokens: 最大输出 token
        temperature: 温度
        **kwargs: 额外参数直接传入请求体
    """
    # system 消息放在最前，然后是 user/assistant/tool 消息
    all_messages = [*system_prompt, *messages]

    # 转换工具 schema
    tool_schemas = [tool_to_api_schema(t) for t in tools] if tools else []

    request: dict = {
        "model": model,
        "messages": all_messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": stream,
    }

    if tool_schemas:
        request["tools"] = tool_schemas

    request.update(kwargs)
    return request
