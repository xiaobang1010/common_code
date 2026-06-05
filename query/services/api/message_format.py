"""消息格式映射模块。

将内部消息格式与 OpenAI Chat Completion API 格式互相转换。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tools.protocol import Tool

from tools.utils.schema import tool_to_openai_schema


# ---------------------------------------------------------------------------
# MessageRole 枚举
# ---------------------------------------------------------------------------


class MessageRole(str, Enum):
    """消息角色枚举。"""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


# ---------------------------------------------------------------------------
# ChatMessage dataclass
# ---------------------------------------------------------------------------


@dataclass
class ChatMessage:
    """内部消息格式。"""

    role: MessageRole
    content: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None
    name: str | None = None


# ---------------------------------------------------------------------------
# to_openai_messages — 内部消息 → OpenAI 格式
# ---------------------------------------------------------------------------


def to_openai_messages(messages: list[ChatMessage]) -> list[dict[str, Any]]:
    """将内部 ChatMessage 列表转换为 OpenAI Chat Completion 消息格式。

    转换规则：
      - system → {"role": "system", "content": ...}
      - user   → {"role": "user", "content": ...}
      - assistant (含 tool_calls) → {"role": "assistant", "content": ..., "tool_calls": [...]}
      - assistant (无 tool_calls) → {"role": "assistant", "content": ...}
      - tool_result → {"role": "tool", "tool_call_id": ..., "content": ...}
    """
    result: list[dict[str, Any]] = []

    for msg in messages:
        if msg.role == MessageRole.SYSTEM:
            result.append({"role": "system", "content": msg.content or ""})

        elif msg.role == MessageRole.USER:
            result.append({"role": "user", "content": msg.content or ""})

        elif msg.role == MessageRole.ASSISTANT:
            entry: dict[str, Any] = {"role": "assistant"}
            if msg.content is not None:
                entry["content"] = msg.content
            elif msg.tool_calls:
                # 有 tool_calls 但无 content 时，使用空字符串避免 API 报错
                entry["content"] = ""
            else:
                entry["content"] = None
            if msg.tool_calls:
                entry["tool_calls"] = msg.tool_calls
            result.append(entry)

        elif msg.role == MessageRole.TOOL:
            result.append({
                "role": "tool",
                "tool_call_id": msg.tool_call_id or "",
                "content": msg.content or "",
            })

    return result


# ---------------------------------------------------------------------------
# to_openai_tools — Tool 列表 → OpenAI function calling 格式
# ---------------------------------------------------------------------------


def to_openai_tools(tools: list[Tool]) -> list[dict[str, Any]]:
    """将 Tool 列表转换为 OpenAI function calling 格式。"""
    return [tool_to_openai_schema(tool) for tool in tools]


# ---------------------------------------------------------------------------
# from_openai_delta — OpenAI SSE delta → ChatMessage
# ---------------------------------------------------------------------------


def from_openai_delta(delta: Any) -> ChatMessage | None:
    """从 OpenAI SSE stream delta 解析消息。

    delta 对象通常具有以下属性：
      - role: str | None
      - content: str | None
      - tool_calls: list | None

    返回 None 表示 delta 无有效内容。
    """
    role_str = getattr(delta, "role", None)
    content = getattr(delta, "content", None)
    tool_calls_raw = getattr(delta, "tool_calls", None)

    # 无内容则跳过
    if role_str is None and content is None and tool_calls_raw is None:
        return None

    # 解析 role
    role: MessageRole | None = None
    if role_str:
        try:
            role = MessageRole(role_str)
        except ValueError:
            role = MessageRole.ASSISTANT  # 流式 delta 默认为 assistant
    else:
        role = MessageRole.ASSISTANT

    # 解析 tool_calls
    tool_calls: list[dict[str, Any]] | None = None
    if tool_calls_raw:
        tool_calls = []
        for tc in tool_calls_raw:
            tc_dict: dict[str, Any] = {
                "id": getattr(tc, "id", None) or "",
                "type": "function",
                "function": {
                    "name": getattr(tc.function, "name", None) or "",
                    "arguments": getattr(tc.function, "arguments", None) or "",
                },
            }
            tool_calls.append(tc_dict)

    return ChatMessage(
        role=role,
        content=content,
        tool_calls=tool_calls if tool_calls else None,
    )
