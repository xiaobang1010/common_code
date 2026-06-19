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
        for attr in ("prompt_tokens", "completion_tokens", "total_tokens"):
            val = getattr(usage, attr, None)
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


# ---------------------------------------------------------------------------
# 测试块
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json

    print("=" * 60)
    print("LLM 流式调用核心测试")
    print("=" * 60)

    # ---- 测试 1: parse_stream_chunk — content delta ----
    print("\n--- 测试 1: parse_stream_chunk — content delta ---")
    try:
        from types import SimpleNamespace

        chunk = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(content="Hello"),
                    finish_reason=None,
                )
            ],
            usage=None,
        )
        events = parse_stream_chunk(chunk)
        assert len(events) == 1, f"期望 1 个事件, 得到 {len(events)}"
        assert events[0].type == "content", f"期望 content, 得到 {events[0].type}"
        assert events[0].content == "Hello", f"期望 'Hello', 得到 {events[0].content}"
        print(f"  事件: type={events[0].type}, content={events[0].content!r}")
        print("  [PASS] content delta 解析")
    except Exception as e:
        print(f"  [FAIL] {e}")

    # ---- 测试 2: parse_stream_chunk — tool_call_delta ----
    print("\n--- 测试 2: parse_stream_chunk — tool_call_delta ---")
    try:
        from types import SimpleNamespace

        chunk = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(
                        content=None,
                        tool_calls=[
                            SimpleNamespace(
                                index=0,
                                id="call_abc123",
                                type="function",
                                function=SimpleNamespace(
                                    name="get_weather",
                                    arguments='{"ci',
                                ),
                            )
                        ],
                    ),
                    finish_reason=None,
                )
            ],
            usage=None,
        )
        events = parse_stream_chunk(chunk)
        assert len(events) == 1, f"期望 1 个事件, 得到 {len(events)}"
        assert events[0].type == "tool_call_delta"
        assert events[0].tool_call_id == "call_abc123"
        assert events[0].tool_call_name == "get_weather"
        assert events[0].tool_call_arguments == '{"ci'
        print(f"  事件: type={events[0].type}, id={events[0].tool_call_id}, "
              f"name={events[0].tool_call_name}, args={events[0].tool_call_arguments!r}")
        print("  [PASS] tool_call_delta 解析")
    except Exception as e:
        print(f"  [FAIL] {e}")

    # ---- 测试 3: parse_stream_chunk — finish_reason ----
    print("\n--- 测试 3: parse_stream_chunk — finish_reason ---")
    try:
        from types import SimpleNamespace

        chunk = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(content=None),
                    finish_reason="stop",
                )
            ],
            usage=None,
        )
        events = parse_stream_chunk(chunk)
        done_events = [e for e in events if e.type == "done"]
        assert len(done_events) == 1, f"期望 1 个 done 事件, 得到 {len(done_events)}"
        assert done_events[0].finish_reason == "stop"
        print(f"  事件: type=done, finish_reason={done_events[0].finish_reason}")
        print("  [PASS] finish_reason 解析")
    except Exception as e:
        print(f"  [FAIL] {e}")

    # ---- 测试 4: parse_stream_chunk — usage ----
    print("\n--- 测试 4: parse_stream_chunk — usage ---")
    try:
        from types import SimpleNamespace

        chunk = SimpleNamespace(
            choices=[],
            usage=SimpleNamespace(
                prompt_tokens=10,
                completion_tokens=20,
                total_tokens=30,
            ),
        )
        events = parse_stream_chunk(chunk)
        usage_events = [e for e in events if e.type == "usage"]
        assert len(usage_events) == 1, f"期望 1 个 usage 事件, 得到 {len(usage_events)}"
        assert usage_events[0].usage == {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}
        print(f"  事件: type=usage, usage={usage_events[0].usage}")
        print("  [PASS] usage 解析")
    except Exception as e:
        print(f"  [FAIL] {e}")

    # ---- 测试 5: parse_stream_chunk — None chunk ----
    print("\n--- 测试 5: parse_stream_chunk — None chunk ---")
    try:
        events = parse_stream_chunk(None)
        assert events == [], f"期望空列表, 得到 {events}"
        print("  [PASS] None chunk 返回空列表")
    except Exception as e:
        print(f"  [FAIL] {e}")

    # ---- 测试 6: collect_tool_calls — 合并增量 ----
    print("\n--- 测试 6: collect_tool_calls — 合并增量 ---")
    try:
        events = [
            # 第一个工具调用的增量
            StreamEvent(
                type="tool_call_delta",
                tool_call_id="call_001",
                tool_call_name="get_weather",
                tool_call_arguments='{"ci',
            ),
            StreamEvent(
                type="tool_call_delta",
                tool_call_id="call_001",
                tool_call_name=None,
                tool_call_arguments='ty": "Beijing"}',
            ),
            # 第二个工具调用的增量
            StreamEvent(
                type="tool_call_delta",
                tool_call_id="call_002",
                tool_call_name="search",
                tool_call_arguments='{"qu',
            ),
            StreamEvent(
                type="tool_call_delta",
                tool_call_id="call_002",
                tool_call_name=None,
                tool_call_arguments='ery": "test"}',
            ),
            # 夹杂的 content 事件应被忽略
            StreamEvent(type="content", content="thinking..."),
        ]

        tool_calls = collect_tool_calls(events)
        assert len(tool_calls) == 2, f"期望 2 个工具调用, 得到 {len(tool_calls)}"

        # 验证第一个工具调用
        tc1 = tool_calls[0]
        assert tc1["id"] == "call_001"
        assert tc1["function"]["name"] == "get_weather"
        assert tc1["function"]["arguments"] == '{"city": "Beijing"}'

        # 验证第二个工具调用
        tc2 = tool_calls[1]
        assert tc2["id"] == "call_002"
        assert tc2["function"]["name"] == "search"
        assert tc2["function"]["arguments"] == '{"query": "test"}'

        for tc in tool_calls:
            print(f"  {json.dumps(tc, ensure_ascii=False)}")
        print("  [PASS] collect_tool_calls 合并增量")
    except Exception as e:
        print(f"  [FAIL] {e}")

    # ---- 测试 7: collect_tool_calls — 无工具调用 ----
    print("\n--- 测试 7: collect_tool_calls — 无工具调用 ---")
    try:
        events = [
            StreamEvent(type="content", content="Hello"),
            StreamEvent(type="done", finish_reason="stop"),
        ]
        tool_calls = collect_tool_calls(events)
        assert tool_calls == [], f"期望空列表, 得到 {tool_calls}"
        print("  [PASS] 无工具调用返回空列表")
    except Exception as e:
        print(f"  [FAIL] {e}")

    # ---- 测试 8: collect_tool_calls — 单个工具调用 ----
    print("\n--- 测试 8: collect_tool_calls — 单个工具调用 ---")
    try:
        events = [
            StreamEvent(
                type="tool_call_delta",
                tool_call_id="call_single",
                tool_call_name="calculate",
                tool_call_arguments='{"expr": "1+1"}',
            ),
        ]
        tool_calls = collect_tool_calls(events)
        assert len(tool_calls) == 1
        assert tool_calls[0]["id"] == "call_single"
        assert tool_calls[0]["function"]["name"] == "calculate"
        assert tool_calls[0]["function"]["arguments"] == '{"expr": "1+1"}'
        print(f"  {json.dumps(tool_calls[0], ensure_ascii=False)}")
        print("  [PASS] 单个工具调用")
    except Exception as e:
        print(f"  [FAIL] {e}")

    # ---- 测试 9: parse_stream_chunk — content + finish_reason 同一 chunk ----
    print("\n--- 测试 9: parse_stream_chunk — content + finish_reason 同一 chunk ---")
    try:
        from types import SimpleNamespace

        chunk = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(content="world"),
                    finish_reason="stop",
                )
            ],
            usage=None,
        )
        events = parse_stream_chunk(chunk)
        assert len(events) == 2, f"期望 2 个事件, 得到 {len(events)}"
        assert events[0].type == "content"
        assert events[0].content == "world"
        assert events[1].type == "done"
        assert events[1].finish_reason == "stop"
        print(f"  事件 1: type=content, content={events[0].content!r}")
        print(f"  事件 2: type=done, finish_reason={events[1].finish_reason}")
        print("  [PASS] content + finish_reason 同一 chunk")
    except Exception as e:
        print(f"  [FAIL] {e}")

    # ---- 测试 10: StreamEvent dataclass ----
    print("\n--- 测试 10: StreamEvent dataclass ---")
    try:
        e1 = StreamEvent(type="content", content="test")
        assert e1.type == "content"
        assert e1.content == "test"
        assert e1.tool_call_id is None
        assert e1.error is None

        e2 = StreamEvent(type="error", error=RuntimeError("boom"))
        assert e2.type == "error"
        assert isinstance(e2.error, RuntimeError)

        e3 = StreamEvent(type="done", finish_reason="stop")
        assert e3.type == "done"
        assert e3.finish_reason == "stop"

        print("  [PASS] StreamEvent dataclass")
    except Exception as e:
        print(f"  [FAIL] {e}")

    print("\n" + "=" * 60)
    print("所有测试完成")
    print("=" * 60)
