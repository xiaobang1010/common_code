"""LLM 流式调用核心模块。

使用 OpenAI 兼容 SDK 进行流式聊天补全调用，
解析 SSE 事件流并生成结构化的 StreamEvent。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, AsyncGenerator

import openai

from query.services.api.client import get_llm_client
from query.services.api.errors import classify_error
from query.services.api.message_format import to_openai_messages, to_openai_tools
from query.services.api.with_retry import RetryConfig, with_retry_stream


# ---------------------------------------------------------------------------
# StreamEvent dataclass
# ---------------------------------------------------------------------------


@dataclass
class StreamEvent:
    """流式事件。

    Attributes:
        type: 事件类型
            - "content": 文本内容增量
            - "tool_call": 完整工具调用
            - "tool_call_delta": 工具调用增量
            - "usage": token 使用量
            - "error": 错误
            - "done": 流结束
        content: 文本内容（type="content" 时）
        tool_call_id: 工具调用 ID
        tool_call_name: 工具名
        tool_call_arguments: 工具参数（JSON 字符串）
        usage: token 使用量字典
        error: 错误对象
        finish_reason: 结束原因（type="done" 时）
    """

    type: str
    content: str | None = None
    tool_call_id: str | None = None
    tool_call_name: str | None = None
    tool_call_arguments: str | None = None
    usage: dict | None = None
    error: Exception | None = None
    finish_reason: str | None = None


# ---------------------------------------------------------------------------
# query_model_with_streaming — 流式调用 LLM
# ---------------------------------------------------------------------------


async def query_model_with_streaming(
    messages: list[Any],
    tools: list[Any] | None = None,
    model: str | None = None,
    **kwargs: Any,
) -> AsyncGenerator[StreamEvent, None]:
    """使用 OpenAI SDK 流式调用 LLM。

    通过 client.chat.completions.create(stream=True) 进行流式调用，
    解析 SSE 事件流并 yield StreamEvent。

    支持同步和异步两种 OpenAI 客户端。

    Args:
        messages: 消息列表（ChatMessage 或 OpenAI 格式）
        tools: 工具列表（Tool 对象或 None）
        model: 模型名（None 时使用默认模型）
        **kwargs: 其他传递给 API 的参数（如 temperature, max_tokens 等）

    Yields:
        StreamEvent: 流式事件
    """
    from query.services.api.client import get_default_model

    if model is None:
        model = get_default_model()

    client = get_llm_client()

    # 构建请求参数
    openai_messages = _build_messages(messages)
    params: dict[str, Any] = {
        "model": model,
        "messages": openai_messages,
        "stream": True,
        "stream_options": {"include_usage": True},
        **kwargs,
    }

    if tools:
        openai_tools = to_openai_tools(tools)
        if openai_tools:
            params["tools"] = openai_tools

    async def _stream_events() -> AsyncGenerator[StreamEvent, None]:
        """内部生成器：创建流并解析 chunk 为 StreamEvent。"""
        # 同步客户端：create() 直接返回 Stream 对象
        stream = client.chat.completions.create(**params)

        for chunk in stream:
            events = parse_stream_chunk(chunk)
            for event in events:
                yield event
            # 让出控制权，避免阻塞事件循环
            await asyncio.sleep(0)

    # 用 with_retry_stream 包装，对建立阶段的可重试错误（rate_limit、server_error）做指数退避重试
    retry_config = RetryConfig()  # 使用默认配置
    try:
        async for event in with_retry_stream(_stream_events, retry_config):
            yield event
    except openai.APIError as e:
        # 不可重试错误或重试耗尽后到这里
        api_error = classify_error(e)
        yield StreamEvent(
            type="error",
            error=e,
            content=api_error.message,
        )
    except Exception as e:
        # 非 openai 异常（如网络层错误），也转为 error 事件
        yield StreamEvent(
            type="error",
            error=e,
            content=str(e),
        )


# ---------------------------------------------------------------------------
# parse_stream_chunk — 解析单个 SSE chunk
# ---------------------------------------------------------------------------


def parse_stream_chunk(chunk: Any) -> list[StreamEvent]:
    """解析单个 SSE chunk 为 StreamEvent 列表。

    一个 chunk 可能包含多个事件（如同时有 content 和 tool_calls）。

    解析规则：
      - delta.content → StreamEvent(type="content", content=...)
      - delta.tool_calls → StreamEvent(type="tool_call_delta", ...)
      - finish_reason → StreamEvent(type="done", finish_reason=...)
      - usage → StreamEvent(type="usage", usage=...)

    Args:
        chunk: OpenAI SDK 流式响应的 chunk 对象

    Returns:
        解析出的 StreamEvent 列表
    """
    events: list[StreamEvent] = []

    # chunk 可能为 None
    if chunk is None:
        return events

    choice = None
    # 从 choices 中取第一个 choice
    choices = getattr(chunk, "choices", None)
    if choices and len(choices) > 0:
        choice = choices[0]

    # ---- 解析 delta ----
    if choice is not None:
        delta = getattr(choice, "delta", None)

        if delta is not None:
            # delta.content → content 事件
            content = getattr(delta, "content", None)
            if content is not None:
                events.append(StreamEvent(
                    type="content",
                    content=content,
                ))

            # delta.tool_calls → tool_call_delta 事件
            tool_calls = getattr(delta, "tool_calls", None)
            if tool_calls:
                for tc in tool_calls:
                    tc_id = getattr(tc, "id", None)
                    tc_function = getattr(tc, "function", None)

                    tc_name = None
                    tc_arguments = None
                    if tc_function is not None:
                        tc_name = getattr(tc_function, "name", None)
                        tc_arguments = getattr(tc_function, "arguments", None)

                    events.append(StreamEvent(
                        type="tool_call_delta",
                        tool_call_id=tc_id,
                        tool_call_name=tc_name,
                        tool_call_arguments=tc_arguments,
                    ))

        # finish_reason → done 事件
        finish_reason = getattr(choice, "finish_reason", None)
        if finish_reason is not None:
            events.append(StreamEvent(
                type="done",
                finish_reason=finish_reason,
            ))

    # ---- 解析 usage ----
    usage = getattr(chunk, "usage", None)
    if usage is not None:
        usage_dict: dict[str, Any] = {}
        # 提取所有可能的 usage 字段（兼容对象属性和字典键两种格式）
        all_attrs = (
            "prompt_tokens", "completion_tokens", "total_tokens",
            "cache_read_input_tokens", "cache_creation_input_tokens",
        )
        for attr in all_attrs:
            val = getattr(usage, attr, None)
            if val is None and isinstance(usage, dict):
                val = usage.get(attr)
            if val is not None:
                usage_dict[attr] = val
        # 只在有真实数据时生成 usage 事件（过滤掉全 0 的占位 usage）
        if usage_dict and any(v > 0 for v in usage_dict.values() if isinstance(v, int)):
            events.append(StreamEvent(
                type="usage",
                usage=usage_dict,
            ))

    return events


# ---------------------------------------------------------------------------
# collect_tool_calls — 从流式事件中收集完整的工具调用
# ---------------------------------------------------------------------------


def collect_tool_calls(events: list[StreamEvent]) -> list[dict[str, Any]]:
    """从流式事件中收集完整的工具调用。

    合并 tool_call_delta 事件为完整 tool_call：
      - 首个带 id 的 delta 创建新条目
      - 后续 delta 追加 name/arguments
      - 最终返回 [{id, function: {name, arguments}}]

    Args:
        events: 流式事件列表

    Returns:
        完整工具调用列表，格式为 OpenAI function calling 格式
    """
    # 使用有序字典按 id 聚合
    tool_calls_map: dict[str, dict[str, Any]] = {}
    # 保持插入顺序
    tool_call_order: list[str] = []

    for event in events:
        if event.type != "tool_call_delta":
            continue

        tc_id = event.tool_call_id

        # 有 id 的 delta → 新工具调用或更新已有条目
        if tc_id:
            if tc_id not in tool_calls_map:
                tool_calls_map[tc_id] = {
                    "id": tc_id,
                    "type": "function",
                    "function": {
                        "name": "",
                        "arguments": "",
                    },
                }
                tool_call_order.append(tc_id)

            entry = tool_calls_map[tc_id]

            # 追加 name（通常只在首个 delta 出现）
            if event.tool_call_name:
                entry["function"]["name"] += event.tool_call_name

            # 追加 arguments（增量拼接）
            if event.tool_call_arguments:
                entry["function"]["arguments"] += event.tool_call_arguments

    # 按插入顺序返回
    return [tool_calls_map[tc_id] for tc_id in tool_call_order]


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _build_messages(messages: list[Any]) -> list[dict[str, Any]]:
    """构建 OpenAI 格式消息列表。

    如果消息已经是 dict 格式则直接使用，
    否则通过 to_openai_messages 转换。
    """
    if not messages:
        return []

    # 如果第一个元素是 dict，假设已经是 OpenAI 格式
    if isinstance(messages[0], dict):
        return messages  # type: ignore[return-value]

    # 否则使用 to_openai_messages 转换
    return to_openai_messages(messages)
