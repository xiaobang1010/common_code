"""LLM 流式调用核心模块。

根据当前激活供应商的 API 格式自动分发到不同路径：
- OpenAI 格式：使用 OpenAI 兼容 SDK 进行流式聊天补全调用
- Anthropic 格式：分发到 anthropic_llm.py 走 httpx 直接请求

两条路径都解析 SSE 事件流并生成统一的 StreamEvent。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, AsyncGenerator

import openai

from query.services.api.client import (
    get_active_api_format,
    get_async_llm_client,
    get_default_model,
)
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
            - "content": 文本内容增量（模型的正式回复）
            - "reasoning": 推理过程增量（DeepSeek 等模型的思维链，和正式回复区分开）
            - "tool_call": 完整工具调用
            - "tool_call_delta": 工具调用增量
            - "usage": token 使用量
            - "context_breakdown": 上下文分类 token 估算（query_loop 发出）
            - "error": 错误
            - "done": 流结束
        content: 文本内容（type="content" 或 type="reasoning" 时）
        tool_call_id: 工具调用 ID
        tool_call_name: 工具名
        tool_call_arguments: 工具参数（JSON 字符串）
        usage: token 使用量字典
        error: 错误对象
        finish_reason: 结束原因（type="done" 时）
        breakdown: 上下文分类估算（type="context_breakdown" 时），
            结构为 {分类名: token 数, "total": 总数}，
            生成逻辑见 query/services/context_metrics.py
    """

    type: str
    content: str | None = None
    tool_call_id: str | None = None
    tool_call_name: str | None = None
    tool_call_arguments: str | None = None
    tool_call_index: int | None = None
    usage: dict | None = None
    error: Exception | None = None
    finish_reason: str | None = None
    breakdown: dict | None = None


# ---------------------------------------------------------------------------
# query_model_with_streaming — 流式调用 LLM
# ---------------------------------------------------------------------------


async def query_model_with_streaming(
    messages: list[Any],
    tools: list[Any] | None = None,
    model: str | None = None,
    **kwargs: Any,
) -> AsyncGenerator[StreamEvent, None]:
    """流式调用 LLM。

    根据当前激活供应商的 API 格式自动分发：
    - "anthropic" -> 走 httpx 直接请求 Anthropic Messages API
    - "openai"（默认）-> 走 OpenAI SDK

    Args:
        messages: 消息列表（ChatMessage 或 OpenAI 格式）
        tools: 工具列表（Tool 对象或 None）
        model: 模型名（None 时使用默认模型）
        **kwargs: 其他传递给 API 的参数（如 temperature, max_tokens 等）

    Yields:
        StreamEvent: 流式事件
    """
    # 检查 API 格式，分发到不同路径
    api_format = get_active_api_format()
    if api_format == "anthropic":
        from query.services.api.anthropic_llm import query_model_with_streaming_anthropic

        async for event in query_model_with_streaming_anthropic(
            messages, tools, model, **kwargs
        ):
            yield event
        return

    if model is None:
        model = get_default_model()

    client = get_async_llm_client()

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
        # 异步客户端：create 与 chunk 读取均为 await/async for，
        # 模型思考间隙会让出事件循环，不再阻塞其他协程
        stream = await client.chat.completions.create(**params)

        async for chunk in stream:
            events = parse_stream_chunk(chunk)
            for event in events:
                yield event

    # 用 with_retry_stream 包装，对建立阶段的可重试错误（rate_limit、server_error、
    # 首包看护超时）做指数退避重试。次数与退避参数统一取 RetryConfig 默认值
    # （对齐主流客户端实践：10 次重试、2s 基础退避倍增、封顶 60s）——
    # 连接类错误快速失败，退避总预算约 6 分钟（不含抖动），可覆盖代理重启等
    # 自愈窗口；每次重试经 _on_retry 的 phase 事件透出进度，界面不静默
    retry_config = RetryConfig()

    async def _on_retry(n: int, total: int, error: Exception) -> StreamEvent:
        # 重试反馈走 phase 事件（前端工作块直接显示），避免重试全程静默
        return StreamEvent(
            type="phase",
            content=f"模型响应超时（{type(error).__name__}），正在重试 {n}/{total}…",
        )

    try:
        async for event in with_retry_stream(
            _stream_events, retry_config, on_retry=_on_retry
        ):
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
      - delta.reasoning_content → StreamEvent(type="reasoning", content=...)
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

            # delta.reasoning_content → 推理过程（DeepSeek R1/V3/V4 的思维链）
            # 和正式回复(content)分开，用独立的 type="reasoning" 事件，
            # 前端可以区分展示（折叠、暗色等）
            reasoning = getattr(delta, "reasoning_content", None)
            if reasoning is not None and reasoning:
                events.append(StreamEvent(
                    type="reasoning",
                    content=reasoning,
                ))

            # delta.tool_calls → tool_call_delta 事件
            tool_calls = getattr(delta, "tool_calls", None)
            if tool_calls:
                for tc in tool_calls:
                    tc_id = getattr(tc, "id", None)
                    tc_index = getattr(tc, "index", None)
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
                        tool_call_index=tc_index,
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
        # 提取标准字段（兼容对象属性和字典键两种格式）
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

        # 兼容 DeepSeek / OpenAI 格式的缓存字段
        # DeepSeek: prompt_cache_hit_tokens（缓存命中）, prompt_cache_miss_tokens（未命中）
        # OpenAI:   prompt_tokens_details.cached_tokens（缓存的 token 数）
        if "cache_read_input_tokens" not in usage_dict:
            cache_hit = getattr(usage, "prompt_cache_hit_tokens", None)
            if cache_hit is None and isinstance(usage, dict):
                cache_hit = usage.get("prompt_cache_hit_tokens")
            if cache_hit is None:
                # 尝试 prompt_tokens_details.cached_tokens
                details = getattr(usage, "prompt_tokens_details", None)
                if details is None and isinstance(usage, dict):
                    details = usage.get("prompt_tokens_details")
                if details is not None:
                    cache_hit = getattr(details, "cached_tokens", None)
                    if cache_hit is None and isinstance(details, dict):
                        cache_hit = details.get("cached_tokens")
            if cache_hit and cache_hit > 0:
                usage_dict["cache_read_input_tokens"] = cache_hit

        if "cache_creation_input_tokens" not in usage_dict:
            cache_miss = getattr(usage, "prompt_cache_miss_tokens", None)
            if cache_miss is None and isinstance(usage, dict):
                cache_miss = usage.get("prompt_cache_miss_tokens")
            if cache_miss and cache_miss > 0:
                usage_dict["cache_creation_input_tokens"] = cache_miss

        # 本次请求实际发送的输入 token 总量，供缓存命中率做分母。
        # OpenAI 兼容协议（含 DeepSeek/阿里等）的 prompt_tokens 已包含命中/未命中的
        # 缓存部分，缓存字段是其子集，故总输入直接取 prompt_tokens——不能再叠加，
        # 否则分母翻倍、命中率显示成真实值的一半
        prompt_tokens = usage_dict.get("prompt_tokens")
        if prompt_tokens is not None:
            usage_dict["total_input_tokens"] = prompt_tokens

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

    按 index 聚合 tool_call_delta 事件：
      - 首个 delta 带 id+name+index，创建新条目
      - 后续 delta 带 index+arguments（id 为 null），追加到对应 index 条目
      - 最终返回 [{id, type, function: {name, arguments}}]，按 index 升序

    Args:
        events: 流式事件列表

    Returns:
        完整工具调用列表，格式为 OpenAI function calling 格式
    """
    # 按 index 聚合，保持插入顺序
    tool_calls_map: dict[int, dict[str, Any]] = {}
    tool_call_order: list[int] = []

    for event in events:
        if event.type != "tool_call_delta":
            continue

        idx = event.tool_call_index
        if idx is None:
            continue

        # 首次见到该 index，创建条目
        if idx not in tool_calls_map:
            tool_calls_map[idx] = {
                "id": "",
                "type": "function",
                "function": {
                    "name": "",
                    "arguments": "",
                },
            }
            tool_call_order.append(idx)

        entry = tool_calls_map[idx]

        # 首个 delta 带 id，记录下来
        if event.tool_call_id:
            entry["id"] = event.tool_call_id

        # 追加 name（通常只在首个 delta 出现）
        if event.tool_call_name:
            entry["function"]["name"] += event.tool_call_name

        # 追加 arguments（增量拼接）
        if event.tool_call_arguments:
            entry["function"]["arguments"] += event.tool_call_arguments

    # 按 index 升序返回
    return [tool_calls_map[idx] for idx in sorted(tool_call_order)]


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
        # 剥离内部元字段（下划线前缀，如 _ts），避免泄漏给模型 API
        return [
            {k: v for k, v in m.items() if not k.startswith("_")}
            for m in messages
        ]

    # 否则使用 to_openai_messages 转换
    return to_openai_messages(messages)
