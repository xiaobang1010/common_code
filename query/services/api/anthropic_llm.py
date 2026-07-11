"""Anthropic Messages API 流式调用模块。

使用 httpx 直接发送 SSE 请求（不依赖 anthropic SDK），
解析 Anthropic 流式事件并转换为统一的 StreamEvent 格式。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from typing import Any, AsyncGenerator

import httpx

from query.services.api.errors import APIError
from query.services.api.llm import StreamEvent
from query.services.api.message_format import to_openai_messages, to_openai_tools
from query.services.api.providers import get_registry
from query.services.api.with_retry import RetryConfig, _calculate_delay

logger = logging.getLogger(__name__)

# Anthropic API 版本
_ANTHROPIC_VERSION = "2023-06-01"

# 默认最大输出 token 数（Anthropic 要求必须指定 max_tokens）
_DEFAULT_MAX_TOKENS = 32768

# OpenAI 特有的参数，不传给 Anthropic
_OPENAI_ONLY_KEYS = frozenset({
    "stream_options",
    "extra_body",
    "extra_headers",
    "n",
    "logprobs",
    "top_logprobs",
})


# ---------------------------------------------------------------------------
# _AnthropicHttpError - Anthropic HTTP 错误
# ---------------------------------------------------------------------------


class _AnthropicHttpError(Exception):
    """Anthropic API HTTP 错误，携带状态码、响应体和响应头。"""

    def __init__(
        self,
        status_code: int,
        body: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self.body = body
        self.headers = headers or {}
        super().__init__(f"HTTP {status_code}: {body}")


# ---------------------------------------------------------------------------
# query_model_with_streaming_anthropic - Anthropic 流式调用主入口
# ---------------------------------------------------------------------------


async def query_model_with_streaming_anthropic(
    messages: list[Any],
    tools: list[Any] | None = None,
    model: str | None = None,
    **kwargs: Any,
) -> AsyncGenerator[StreamEvent, None]:
    """使用 httpx 流式调用 Anthropic Messages API。

    直接发送 SSE 请求，解析 Anthropic 流式事件并转换为 StreamEvent。

    和 OpenAI 路径的行为保持一致：
    - 建立连接阶段（首个事件前）遇到 rate_limit / server_error 会自动重试
    - 流开始后不再重试，错误转为 error 事件
    - 所有异常最终都转为 StreamEvent，不向调用方抛出

    Args:
        messages: 消息列表（ChatMessage 或 OpenAI 格式 dict）
        tools: 工具列表（Tool 对象或 OpenAI 格式 dict）
        model: 模型名（None 时使用默认模型）
        **kwargs: 其他参数（如 temperature, max_tokens, top_p 等）

    Yields:
        StreamEvent: 流式事件
    """
    from query.services.api.client import get_default_model

    # 1. 获取连接配置
    base_url, api_key, default_model = _get_anthropic_config()
    if model is None:
        model = default_model or get_default_model()

    # 2. 构建消息（先转成 OpenAI dict，再转成 Anthropic 格式）
    openai_messages = _build_openai_messages(messages)
    system_prompt, anthropic_messages = _to_anthropic_messages(openai_messages)

    # 3. 构建请求体
    max_tokens = kwargs.pop("max_tokens", _DEFAULT_MAX_TOKENS)
    payload: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": anthropic_messages,
        "stream": True,
    }
    if system_prompt:
        payload["system"] = system_prompt

    # 4. 构建工具列表
    if tools:
        openai_tools = _build_openai_tools(tools)
        anthropic_tools = _to_anthropic_tools(openai_tools)
        if anthropic_tools:
            payload["tools"] = anthropic_tools

    # 5. 合并其他参数（过滤 OpenAI 特有的）
    _merge_kwargs(payload, kwargs)

    # 6. 构建请求头和 URL
    headers = _build_headers(api_key)
    url = f"{base_url.rstrip('/')}/v1/messages"

    # 7. 构建 httpx 客户端参数
    client_kwargs = _build_client_kwargs()

    # 8. 带重试的流式调用
    # 和 OpenAI 路径一样：只在建立阶段（首个事件 yield 前）重试，
    # 流开始后不重试（避免重复输出）
    retry_config = RetryConfig()

    async def _stream_events() -> AsyncGenerator[StreamEvent, None]:
        """内部生成器：建立连接并解析 SSE 事件。"""
        async with httpx.AsyncClient(**client_kwargs) as client:
            async with client.stream(
                "POST", url, json=payload, headers=headers
            ) as response:
                # HTTP 状态码非 200 -> 读取错误信息并抛出
                if response.status_code != 200:
                    body_bytes = await response.aread()
                    error_text = body_bytes.decode("utf-8", errors="replace")
                    raise _AnthropicHttpError(
                        status_code=response.status_code,
                        body=error_text,
                        headers=dict(response.headers),
                    )

                # 正常响应 -> 逐行解析 SSE 事件
                async for data in _iter_sse_events(response):
                    for event in _parse_anthropic_event(data):
                        yield event

    for attempt in range(retry_config.max_retries + 1):
        gen = _stream_events()
        try:
            # 尝试获取首个事件——这是重试窗口
            first_event = await gen.__anext__()
        except StopAsyncIteration:
            # 空 generator，正常结束
            return
        except Exception as error:
            api_error = _classify_anthropic_error(error)

            # 不可重试或重试耗尽 -> 转为 error 事件
            if (
                api_error.type not in retry_config.retryable_errors
                or attempt >= retry_config.max_retries
            ):
                yield StreamEvent(
                    type="error",
                    error=error,
                    content=api_error.message,
                )
                return

            # 可重试 -> 指数退避等待后重试
            delay = _calculate_delay(
                attempt=attempt,
                base_delay=retry_config.base_delay,
                max_delay=retry_config.max_delay,
                api_error=api_error,
            )
            logger.warning(
                "Anthropic 流式调用失败（第 %d 次），%.1f 秒后重试: %s",
                attempt + 1,
                delay,
                api_error.message,
            )
            await asyncio.sleep(delay)
            continue

        # 首个事件成功，重试窗口关闭
        yield first_event

        # 继续迭代剩余事件——这里不再重试
        try:
            async for event in gen:
                yield event
        except Exception as error:
            # 流式过程中出错，不重试，直接转为 error 事件
            api_error = _classify_anthropic_error(error)
            yield StreamEvent(
                type="error",
                error=error,
                content=api_error.message,
            )
        return


# ---------------------------------------------------------------------------
# 配置获取
# ---------------------------------------------------------------------------


def _get_anthropic_config() -> tuple[str, str, str]:
    """获取 Anthropic API 连接配置。

    从供应商注册表获取 base_url、api_key、model，
    若注册表无激活供应商则回退到环境变量/配置文件。

    Returns:
        (base_url, api_key, model)
    """
    from query.services.api.client import (
        _resolve_api_key,
        _resolve_base_url,
        get_default_model,
    )

    registry = get_registry()
    provider = registry.get_active_provider()
    if provider:
        return provider["base_url"], provider.get("api_key", ""), provider.get("model", "")

    # 回退到环境变量/配置文件
    return _resolve_base_url(), _resolve_api_key() or "", get_default_model()


# ---------------------------------------------------------------------------
# 消息格式转换
# ---------------------------------------------------------------------------


def _build_openai_messages(messages: list[Any]) -> list[dict[str, Any]]:
    """将消息列表转换为 OpenAI 格式 dict 列表。

    如果消息已经是 dict 格式则直接使用，
    否则通过 to_openai_messages 转换。
    """
    if not messages:
        return []
    if isinstance(messages[0], dict):
        return messages  # type: ignore[return-value]
    return to_openai_messages(messages)


def _to_anthropic_messages(
    openai_messages: list[dict[str, Any]],
) -> tuple[str | None, list[dict[str, Any]]]:
    """将 OpenAI 格式消息转换为 Anthropic 格式。

    Anthropic 和 OpenAI 的消息格式主要差异：
    - system 消息在 Anthropic 中单独放在 system 字段，不在 messages 数组里
    - 工具调用在 Anthropic 中是 content block（tool_use），不是顶层字段
    - 工具结果在 Anthropic 中是 user 消息里的 tool_result content block
    - 连续多条 tool 结果消息要合并到一个 user 消息里

    Returns:
        (system_prompt, anthropic_messages)
    """
    system_parts: list[str] = []
    anthropic_messages: list[dict[str, Any]] = []

    i = 0
    while i < len(openai_messages):
        msg = openai_messages[i]
        role = msg.get("role", "")
        content = msg.get("content")

        if role == "system":
            # system 消息提取出来，拼到 system 字段
            if content:
                system_parts.append(str(content))
            i += 1

        elif role == "user":
            anthropic_messages.append({
                "role": "user",
                "content": content if content is not None else "",
            })
            i += 1

        elif role == "assistant":
            # assistant 消息可能包含 tool_calls
            tool_calls = msg.get("tool_calls")
            if tool_calls:
                blocks: list[dict[str, Any]] = []
                if content:
                    blocks.append({"type": "text", "text": str(content)})
                for tc in tool_calls:
                    tc_id = tc.get("id", "")
                    function = tc.get("function", {})
                    name = function.get("name", "")
                    arguments = function.get("arguments", "{}")
                    # Anthropic 的 input 是 dict，不是 JSON 字符串
                    try:
                        input_dict = json.loads(arguments) if arguments else {}
                    except (json.JSONDecodeError, TypeError):
                        input_dict = {}
                    blocks.append({
                        "type": "tool_use",
                        "id": tc_id,
                        "name": name,
                        "input": input_dict,
                    })
                anthropic_messages.append({"role": "assistant", "content": blocks})
            else:
                anthropic_messages.append({
                    "role": "assistant",
                    "content": content if content is not None else "",
                })
            i += 1

        elif role == "tool":
            # OpenAI 的 tool 结果消息 -> Anthropic 的 user 消息带 tool_result block
            # 连续多条 tool 消息合并到一个 user 消息里（Anthropic 要求）
            tool_results: list[dict[str, Any]] = []
            while i < len(openai_messages) and openai_messages[i].get("role") == "tool":
                tool_msg = openai_messages[i]
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_msg.get("tool_call_id", ""),
                    "content": tool_msg.get("content") or "",
                })
                i += 1
            anthropic_messages.append({
                "role": "user",
                "content": tool_results,
            })

        else:
            # 未知角色，跳过
            i += 1

    system = "\n\n".join(system_parts) if system_parts else None
    return system, anthropic_messages


# ---------------------------------------------------------------------------
# 工具格式转换
# ---------------------------------------------------------------------------


def _build_openai_tools(tools: list[Any]) -> list[dict[str, Any]]:
    """将工具列表转换为 OpenAI 格式。

    如果工具已经是 dict 格式则直接使用，
    否则通过 to_openai_tools 转换。
    """
    if not tools:
        return []
    if isinstance(tools[0], dict):
        return tools  # type: ignore[return-value]
    return to_openai_tools(tools)


def _to_anthropic_tools(openai_tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """将 OpenAI function calling 格式的工具列表转换为 Anthropic 格式。

    OpenAI:   {"type": "function", "function": {"name": ..., "description": ..., "parameters": ...}}
    Anthropic: {"name": ..., "description": ..., "input_schema": ...}
    """
    result: list[dict[str, Any]] = []
    for tool in openai_tools:
        # 兼容完整 OpenAI tool 格式和直接传 function 字典两种情况
        function = tool.get("function", tool)
        result.append({
            "name": function.get("name", ""),
            "description": function.get("description", ""),
            "input_schema": function.get(
                "parameters",
                function.get("input_schema", {"type": "object", "properties": {}}),
            ),
        })
    return result


# ---------------------------------------------------------------------------
# kwargs 合并
# ---------------------------------------------------------------------------


def _merge_kwargs(payload: dict[str, Any], kwargs: dict[str, Any]) -> None:
    """将额外的 kwargs 合并到请求体中，过滤 OpenAI 特有参数。

    同时做参数名映射：
    - stop -> stop_sequences
    """
    for key, value in kwargs.items():
        if key in _OPENAI_ONLY_KEYS:
            continue
        if key == "stop":
            # OpenAI 用 stop，Anthropic 用 stop_sequences
            payload["stop_sequences"] = value
        else:
            payload[key] = value


# ---------------------------------------------------------------------------
# 请求头和客户端构建
# ---------------------------------------------------------------------------


def _build_headers(api_key: str) -> dict[str, str]:
    """构建 Anthropic API 请求头。"""
    from query.services.api.client import CLIENT_REQUEST_ID_HEADER, get_custom_headers

    headers: dict[str, str] = {
        "x-api-key": api_key,
        "anthropic-version": _ANTHROPIC_VERSION,
        "content-type": "application/json",
        CLIENT_REQUEST_ID_HEADER: str(uuid.uuid4()),
    }
    # 注入自定义 header（CC_CUSTOM_HEADERS 环境变量）
    headers.update(get_custom_headers())
    return headers


def _build_client_kwargs() -> dict[str, Any]:
    """构建 httpx.AsyncClient 参数（超时、代理）。"""
    kwargs: dict[str, Any] = {
        "timeout": httpx.Timeout(600.0, connect=10.0),
    }
    proxy = (
        os.environ.get("HTTPS_PROXY")
        or os.environ.get("https_proxy")
        or os.environ.get("HTTP_PROXY")
        or os.environ.get("http_proxy")
    )
    if proxy:
        kwargs["proxy"] = proxy
    return kwargs


# ---------------------------------------------------------------------------
# SSE 事件流解析
# ---------------------------------------------------------------------------


async def _iter_sse_events(response: httpx.Response) -> AsyncGenerator[dict, None]:
    """从 httpx 流式响应中解析 SSE 事件，yield JSON dict。

    SSE 格式：
      event: <event_type>
      data: <json_data>

      （空行分隔事件）

    Anthropic 的 data 行里 JSON 自带 type 字段，所以不需要 event 行。
    """
    data_lines: list[str] = []

    async for line in response.aiter_lines():
        line = line.rstrip("\r\n")

        if not line:
            # 空行 -> 事件边界，处理累积的 data
            if data_lines:
                data_str = "\n".join(data_lines)
                data_lines.clear()
                try:
                    data = json.loads(data_str)
                    yield data
                except json.JSONDecodeError:
                    pass
            continue

        if line.startswith(":"):
            # SSE 注释行，跳过
            continue

        if line.startswith("data:"):
            data_lines.append(line[5:].strip())
        # event: 行不需要单独处理，data JSON 中已有 type 字段

    # 处理最后可能残留的事件（流结束时没有空行结尾）
    if data_lines:
        data_str = "\n".join(data_lines)
        try:
            data = json.loads(data_str)
            yield data
        except json.JSONDecodeError:
            pass


def _parse_anthropic_event(data: dict[str, Any]) -> list[StreamEvent]:
    """将 Anthropic SSE 事件数据转换为 StreamEvent 列表。

    事件类型映射：
      - message_start             -> usage 事件（初始 token 用量，主要是 input_tokens）
      - content_block_start        -> tool_call_delta 事件（tool_use 块开始时带 id 和 name）
      - content_block_delta        -> content / tool_call_delta / reasoning 事件
      - message_delta             -> done 事件（含 finish_reason）+ usage 事件
      - error                     -> error 事件
      - message_stop / ping / content_block_stop -> 忽略
    """
    events: list[StreamEvent] = []
    event_type = data.get("type")

    if event_type == "message_start":
        # 消息开始，包含初始 usage（主要是 input_tokens）
        message = data.get("message", {})
        usage = message.get("usage", {})
        if usage:
            converted = _convert_anthropic_usage(usage)
            if converted:
                events.append(StreamEvent(type="usage", usage=converted))

    elif event_type == "content_block_start":
        content_block = data.get("content_block", {})
        block_type = content_block.get("type")
        if block_type == "tool_use":
            # 工具调用开始 -> 发出 tool_call_delta 带 id 和 name
            events.append(StreamEvent(
                type="tool_call_delta",
                tool_call_id=content_block.get("id"),
                tool_call_name=content_block.get("name"),
                tool_call_arguments="",
            ))
        # text / thinking block 开始时内容为空，不需要发事件

    elif event_type == "content_block_delta":
        delta = data.get("delta", {})
        delta_type = delta.get("type")

        if delta_type == "text_delta":
            text = delta.get("text", "")
            if text:
                events.append(StreamEvent(type="content", content=text))

        elif delta_type == "input_json_delta":
            # 工具调用的参数增量（JSON 片段）
            partial = delta.get("partial_json", "")
            if partial:
                events.append(StreamEvent(
                    type="tool_call_delta",
                    tool_call_arguments=partial,
                ))

        elif delta_type == "thinking_delta":
            # 推理过程增量（Claude 的思维链），和正式回复区分开
            thinking = delta.get("thinking", "")
            if thinking:
                events.append(StreamEvent(type="reasoning", content=thinking))

        # signature_delta 等其他 delta 类型忽略

    elif event_type == "message_delta":
        # 消息增量，含 stop_reason 和可能的 usage（output_tokens）
        delta = data.get("delta", {})
        stop_reason = delta.get("stop_reason")
        if stop_reason:
            events.append(StreamEvent(
                type="done",
                finish_reason=_map_stop_reason(stop_reason),
            ))
        usage = data.get("usage", {})
        if usage:
            converted = _convert_anthropic_usage(usage)
            if converted:
                events.append(StreamEvent(type="usage", usage=converted))

    elif event_type == "error":
        # API 在流中返回错误事件
        error = data.get("error", {})
        error_msg = error.get("message", "未知 Anthropic 错误")
        events.append(StreamEvent(
            type="error",
            content=error_msg,
            error=Exception(error_msg),
        ))

    # message_stop / content_block_stop / ping 等不需要处理

    return events


def _map_stop_reason(stop_reason: str) -> str:
    """将 Anthropic stop_reason 映射为 OpenAI finish_reason。

    映射关系：
      - end_turn      -> stop       （正常结束）
      - max_tokens     -> length     （达到最大 token 数）
      - tool_use       -> tool_calls （调用了工具）
      - stop_sequence  -> stop       （命中停止序列）
    """
    mapping = {
        "end_turn": "stop",
        "max_tokens": "length",
        "tool_use": "tool_calls",
        "stop_sequence": "stop",
    }
    return mapping.get(stop_reason, stop_reason)


def _convert_anthropic_usage(usage: dict[str, Any]) -> dict[str, Any]:
    """将 Anthropic usage 格式转换为统一格式。

    Anthropic: {"input_tokens": 10, "output_tokens": 50}
    统一格式:  {"prompt_tokens": 10, "completion_tokens": 50, "total_tokens": 60}

    同时保留 Anthropic 原生的缓存字段：
      - cache_read_input_tokens      （缓存命中）
      - cache_creation_input_tokens  （缓存写入）
    """
    result: dict[str, Any] = {}
    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")

    if input_tokens is not None:
        result["prompt_tokens"] = input_tokens
    if output_tokens is not None:
        result["completion_tokens"] = output_tokens
    if input_tokens is not None and output_tokens is not None:
        result["total_tokens"] = input_tokens + output_tokens

    # 缓存相关字段（Anthropic 原生支持）
    cache_read = usage.get("cache_read_input_tokens")
    if cache_read and cache_read > 0:
        result["cache_read_input_tokens"] = cache_read
    cache_creation = usage.get("cache_creation_input_tokens")
    if cache_creation and cache_creation > 0:
        result["cache_creation_input_tokens"] = cache_creation

    return result


# ---------------------------------------------------------------------------
# 错误分类
# ---------------------------------------------------------------------------


def _classify_anthropic_error(error: Exception) -> APIError:
    """将 Anthropic API 错误分类为 APIError。

    用于判断是否可重试（rate_limit / server_error 可重试）。

    分类规则：
      - HTTP 429               -> rate_limit（可重试）
      - HTTP 5xx               -> server_error（可重试）
      - HTTP 401/403           -> auth_error（不可重试）
      - HTTP 400 + context     -> context_length_exceeded（不可重试）
      - HTTP 400 其他           -> unknown（不可重试）
      - httpx 网络/超时错误     -> server_error（可重试）
      - 其他                   -> unknown（不可重试）
    """
    if isinstance(error, _AnthropicHttpError):
        status = error.status_code
        body = error.body

        if status == 429:
            retry_after = _extract_retry_after(error.headers)
            return APIError(
                type="rate_limit",
                message=body,
                status_code=status,
                retry_after=retry_after,
            )
        if status >= 500:
            return APIError(type="server_error", message=body, status_code=status)
        if status in (401, 403):
            return APIError(type="auth_error", message=body, status_code=status)
        if status == 400:
            msg_lower = body.lower()
            if "context" in msg_lower and "length" in msg_lower:
                return APIError(
                    type="context_length_exceeded",
                    message=body,
                    status_code=status,
                )
            return APIError(type="unknown", message=body, status_code=status)
        return APIError(type="unknown", message=body, status_code=status)

    # httpx 网络错误（连接失败、超时、读取错误）-> server_error（可重试）
    if isinstance(error, (httpx.ConnectError, httpx.TimeoutException, httpx.ReadError)):
        return APIError(type="server_error", message=str(error), status_code=None)

    return APIError(type="unknown", message=str(error), status_code=None)


def _extract_retry_after(headers: dict[str, str]) -> float | None:
    """从 HTTP 响应头中提取 retry-after 值（秒）。"""
    if not headers:
        return None
    try:
        value = headers.get("retry-after") or headers.get("Retry-After")
        if value:
            return float(value)
    except (ValueError, TypeError):
        pass
    return None
