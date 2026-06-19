"""Agentic 循环引擎核心。

Agentic 循环引擎核心。

核心查询入口和 while(true) 无限循环，
每轮迭代执行：压缩 → 构建请求 → 调用模型 → 流式输出 →
工具调用 → 追加结果 → 错误恢复 → 完成检查 → 状态转换。

三层结构：
  - QueryEngine：会话级状态（消息历史、token 用量、轮次）
  - QueryConfig：循环级快照（session_id、auto_compact_enabled 等）
  - QueryDeps：I/O 依赖（call_model、microcompact、autocompact 等）
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any, AsyncGenerator

from query.config import QueryConfig, build_query_config
from query.deps import QueryDeps
from query.stop_hooks import run_stop_hooks
from query.services.api.errors import APIError, classify_error, is_recoverable_error
from query.services.api.llm import StreamEvent, collect_tool_calls
from query.services.compact.auto_compact import CompactTracking
from tools.executor import (
    ToolExecutionResult,
    tool_result_to_openai_message,
)
from query.utils.api import build_api_request, prepend_user_context, append_system_context

if TYPE_CHECKING:
    from query.engine import QueryEngine


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# max_output_tokens 恢复重试上限
MAX_OUTPUT_TOKENS_RECOVERY_LIMIT = 3

# 上下文长度超限时的错误关键词
_CONTEXT_LENGTH_KEYWORDS = ("context_length", "maximum context length", "prompt too long")


# ---------------------------------------------------------------------------
# LoopResult — query_loop 退出结果
# ---------------------------------------------------------------------------


@dataclass
class LoopResult:
    """query_loop 退出结果，标明退出原因。

    Attributes:
        reason: 退出原因
            - "completed": 正常完成（finish_reason=stop 或无工具调用）
            - "prompt_too_long": 上下文超限且恢复失败
            - "model_error": 模型调用异常（不可恢复错误、流异常）
            - "tool_error": 工具执行异常
            - "max_output_tokens_exhausted": 输出 token 恢复次数用尽
        error: 异常对象（reason 为错误类时携带）
    """

    reason: str
    error: Exception | None = None


# ---------------------------------------------------------------------------
# State — 循环内临时状态
# ---------------------------------------------------------------------------


@dataclass
class State:
    """循环内临时状态，每轮迭代可能重建。

    会话级状态（messages、total_usage、turn_count）已迁移到 QueryEngine，
    这里只保留循环内临时状态。

    每次循环迭代开始时解构，continue 时整体赋值：
      state = State(**{**asdict(state), **updates})

    Attributes:
        transition: 转换原因（防止死循环）
        error_count: 错误计数
        withheld_messages: 被暂扣的消息（可恢复错误恢复前暂不输出）
        max_output_tokens_recovery_count: max_output_tokens 恢复计数
        has_attempted_reactive_compact: 是否已尝试响应式压缩
        auto_compact_tracking: 自动压缩追踪状态
    """

    transition: str | None = None
    error_count: int = 0
    withheld_messages: list[dict] = field(default_factory=list)
    max_output_tokens_recovery_count: int = 0
    has_attempted_reactive_compact: bool = False
    auto_compact_tracking: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# _is_context_length_error — 判断是否为上下文长度错误
# ---------------------------------------------------------------------------


def _is_context_length_error(error: Exception | APIError) -> bool:
    """判断错误是否为上下文长度超限。"""
    msg = str(error).lower()
    return any(kw in msg for kw in _CONTEXT_LENGTH_KEYWORDS)


# ---------------------------------------------------------------------------
# _is_max_output_tokens_event — 判断是否为 max_output_tokens 事件
# ---------------------------------------------------------------------------


def _is_max_output_tokens_event(event: StreamEvent) -> bool:
    """判断流式事件是否表示输出 token 超限。"""
    return (
        event.type == "done"
        and event.finish_reason == "length"
    )


# ---------------------------------------------------------------------------
# _build_tool_result_messages — 将工具执行结果转为消息
# ---------------------------------------------------------------------------


def _build_tool_result_messages(
    results: list[ToolExecutionResult],
) -> list[dict]:
    """将工具执行结果列表转换为 OpenAI 格式消息。"""
    messages: list[dict] = []
    for result in results:
        msg = tool_result_to_openai_message(result)
        messages.append(msg)
    return messages


# ---------------------------------------------------------------------------
# _build_assistant_message — 从流式事件构建 assistant 消息
# ---------------------------------------------------------------------------


def _build_assistant_message(
    content_parts: list[str],
    tool_calls: list[dict],
) -> dict:
    """从流式事件中收集的内容和工具调用构建 assistant 消息。"""
    content = "".join(content_parts) if content_parts else ""
    msg: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return msg


# ---------------------------------------------------------------------------
# _run_inline_compression — 内联四级压缩管线
# ---------------------------------------------------------------------------


async def _run_inline_compression(
    messages: list[dict],
    model: str,
    tracking: CompactTracking,
    context_collapse_enabled: bool,
    deps: Any,
) -> list[dict]:
    """内联四级压缩管线。

    顺序：snip → microcompact → context_collapse → autocompact。
    snip 和 microcompact 无条件执行（不互斥），
    autocompact 内部自判阈值，管线层面不做提前返回
    （原来 run_compression_pipeline 每级之间的 safe_threshold 提前返回已移除）。

    token 估算：粗略方式（字符数 ÷ 4），后续可替换为 tiktoken 等精确估算。

    Args:
        messages: 消息列表
        model: 模型名称
        tracking: 压缩追踪状态
        context_collapse_enabled: 是否启用 context collapse
        deps: I/O 依赖（取 microcompact、autocompact）

    Returns:
        压缩后的消息列表
    """
    import os

    from query.services.compact.snip import (
        _estimate_tokens_for_messages as _est_tokens,
        should_snip,
        snip_messages,
    )
    from query.services.compact.micro_compact import should_micro_compact
    from query.services.compact.context_collapse import (
        context_collapse_messages,
        should_context_collapse,
    )
    from startup.utils.model.config import get_effective_context_window
    from query.utils.messages import get_messages_after_compact_boundary

    # token 估算基于切片后的活跃窗口（最后一个 boundary 之后的消息），
    # 而非完整历史。REPL 传入的 messages 可能含已被压缩的旧消息，
    # 那些不会发给 LLM，不应计入 token 估算。
    context_window = get_effective_context_window(model)
    active_messages = get_messages_after_compact_boundary(messages)
    current_tokens = _est_tokens(active_messages)

    # 1a. Snip（受 COMMON_CODE_ENABLE_SNIP 环境变量门控）
    snip_enabled = os.environ.get("COMMON_CODE_ENABLE_SNIP", "").lower() in (
        "1", "true", "yes", "on",
    )
    if snip_enabled and should_snip(messages, context_window, current_tokens):
        messages, _snip_tokens_freed = snip_messages(
            messages, context_window, current_tokens,
        )
        current_tokens = _est_tokens(messages)

    # 1b. Microcompact（无条件执行，snip 和 microcompact 不互斥）
    if should_micro_compact(messages):
        messages = deps.microcompact(messages=messages)

    # 1c. Context Collapse（受 context_collapse_enabled 门控）
    if context_collapse_enabled and should_context_collapse(
        messages, context_window, current_tokens,
    ):
        messages = await context_collapse_messages(
            messages, model, context_window,
        )

    # 1d. Autocompact（内部自判阈值，不在管线层面做提前返回）
    messages, _was_compacted = await deps.autocompact(
        messages=messages,
        model=model,
        tracking=tracking,
        context_collapse_enabled=context_collapse_enabled,
    )

    return messages


# ---------------------------------------------------------------------------
# query — 便捷入口
# ---------------------------------------------------------------------------


async def query(
    params: dict[str, Any] | None = None,
) -> AsyncGenerator[StreamEvent | dict, None]:
    """便捷入口，内部创建一次性 QueryEngine。

    query() 是一次性调用，不传 prompt（messages 已包含历史），
    直接调 query_loop。如需跨轮持久化，请使用 QueryEngine.submitMessage。

    Args:
        params: 查询参数，支持以下字段：
            - messages: 初始消息列表
            - config_overrides: QueryEngineConfig 覆盖字段（含 deps）
            - user_context: 用户上下文字典，由调用方构建（dict[str, str] | None）
            - system_context: 系统上下文字典，由调用方构建（dict[str, str] | None）

    Yields:
        StreamEvent | dict: 流式事件或结果消息
    """
    # 延迟 import 避免循环依赖
    from query.engine import build_engine_config, QueryEngine

    if params is None:
        params = {}

    messages = params.get("messages", [])
    config_overrides = params.get("config_overrides", {})
    user_context = params.get("user_context")
    system_context = params.get("system_context")

    engine_config = build_engine_config(**config_overrides)
    engine = QueryEngine(engine_config, initial_messages=messages)

    # query() 是一次性调用，不需要 prompt（messages 已包含历史）
    # 直接调 query_loop
    query_config = build_query_config(session_id=engine.deps.get_uuid())
    async for event in query_loop(engine, query_config, user_context, system_context):
        yield event


# ---------------------------------------------------------------------------
# query_loop — while(true) 无限循环
# ---------------------------------------------------------------------------


async def query_loop(
    engine: QueryEngine,
    config: QueryConfig,
    user_context: dict[str, str] | None = None,
    system_context: dict[str, str] | None = None,
) -> AsyncGenerator[StreamEvent | dict, None]:
    """Agentic 循环核心。

    while(true) 无限循环，每轮迭代：
    1. 压缩管线
    2. 注入用户/系统上下文（由调用方传入）
    3. 构建 API 请求
    4. 调用模型（流式）
    5. 流式输出
    6. 检测工具调用
    7. 错误恢复
    8. 完成检查
    9. 执行工具
    10. 追加结果到消息列表
    11. 状态转换

    会话级状态（messages、total_usage）从 engine 读写，
    循环级临时状态从 state 读写，I/O 依赖从 engine.deps 获取。

    Args:
        engine: 查询引擎，持有会话状态和会话级配置
        config: 循环级配置快照（auto_compact_enabled 等）
        user_context: 用户上下文字典，由调用方构建
        system_context: 系统上下文字典，由调用方构建

    Yields:
        StreamEvent | dict: 流式事件或结果消息
    """
    deps = engine.deps
    engine_config = engine.config

    # 初始化压缩追踪
    tracking: CompactTracking = CompactTracking()

    # 循环内临时状态
    state = State()

    # eslint-disable-next-line no-constant-condition
    while True:
        # 从引擎读取当前消息
        messages = engine.mutable_messages
        transition = state.transition

        # ---- 1. 压缩管线（内联四级）----
        if config.auto_compact_enabled and messages:
            try:
                compacted = await _run_inline_compression(
                    messages=messages,
                    model=engine_config.model,
                    tracking=tracking,
                    context_collapse_enabled=config.context_collapse_enabled,
                    deps=deps,
                )
                if compacted is not None and compacted != messages:
                    # 先 yield 压缩产物（boundary marker + summary + kept messages），
                    # 让 REPL 据此更新自己的完整历史。
                    for msg in compacted:
                        yield msg
                    # 压缩后替换引擎消息
                    engine.mutable_messages = compacted
                    messages = compacted
                    # 重置追踪
                    tracking = CompactTracking()
            except Exception:
                # 压缩失败不中断循环
                pass

        # ---- 2. 用户/系统上下文（由调用方传入，无需此处获取） ----

        # ---- 3. 构建 API 请求 ----
        from startup.constants.prompts import build_system_messages, get_system_prompt_sections

        sections = engine_config.system_prompt_sections or get_system_prompt_sections()
        system_messages = build_system_messages(sections)

        if system_context:
            system_messages = append_system_context(system_messages, system_context)

        # 用户上下文仅临时拼入 api_messages，不污染 messages（messages 会被写回引擎）
        api_messages = messages
        if user_context:
            api_messages = prepend_user_context(api_messages, user_context)

        request = build_api_request(
            messages=api_messages,
            system_prompt=system_messages,
            tools=engine_config.tools,
            model=engine_config.model,
            max_tokens=engine_config.max_tokens,
            temperature=engine_config.temperature,
        )

        # ---- 4. 调用模型（流式） ----
        yield StreamEvent(type="content", content="")  # stream_request_start 信号

        content_parts: list[str] = []
        stream_events: list[StreamEvent] = []
        finish_reason: str | None = None
        usage_info: dict | None = None
        error_occurred: Exception | None = None
        # 扣留的上下文超限错误事件，恢复完才决定要不要暴露给调用方
        withheld_error: StreamEvent | None = None

        try:
            async for event in deps.call_model(
                messages=request["messages"],
                tools=engine_config.tools,
                model=engine_config.model,
                max_tokens=engine_config.max_tokens,
                temperature=engine_config.temperature,
            ):
                # ---- 5. 流式输出 ----
                # 上下文超限错误先扣下，等恢复流程走完再决定是否暴露
                if (
                    event.type == "error"
                    and event.error
                    and _is_context_length_error(event.error)
                ):
                    withheld_error = event
                    stream_events.append(event)
                    error_occurred = event.error
                    continue

                # 非上下文超限的事件正常 yield 和处理
                yield event
                stream_events.append(event)

                if event.type == "content" and event.content:
                    content_parts.append(event.content)
                elif event.type == "done" and event.finish_reason:
                    finish_reason = event.finish_reason
                elif event.type == "usage" and event.usage:
                    usage_info = event.usage
                elif event.type == "error" and event.error:
                    error_occurred = event.error

        except Exception as e:
            # 模型调用异常
            yield StreamEvent(
                type="error",
                error=e,
                content=str(e),
            )
            yield LoopResult(reason="model_error", error=e)
            return

        # 更新 token 使用量（写回引擎）
        if usage_info:
            engine.total_usage += usage_info.get("total_tokens", 0)

        # ---- 6. 检测工具调用 ----
        tool_calls = collect_tool_calls(stream_events)

        # 构建 assistant 消息
        assistant_msg = _build_assistant_message(content_parts, tool_calls)

        # ---- 7. 错误恢复 ----

        # 7a. 上下文长度超限 → 尝试压缩恢复
        if error_occurred and _is_context_length_error(error_occurred):
            api_error = classify_error(error_occurred)
            if is_recoverable_error(api_error):
                # 尝试压缩恢复
                if not state.has_attempted_reactive_compact and config.auto_compact_enabled:
                    try:
                        compacted = await _run_inline_compression(
                            messages=messages,
                            model=engine_config.model,
                            tracking=tracking,
                            context_collapse_enabled=config.context_collapse_enabled,
                            deps=deps,
                        )
                        if compacted is not None and compacted != messages:
                            engine.mutable_messages = compacted
                            messages = compacted
                            updates = {
                                "has_attempted_reactive_compact": True,
                                "transition": "reactive_compact_retry",
                            }
                            state = State(**{**asdict(state), **updates})
                            continue
                    except Exception:
                        pass

                # 压缩恢复失败 → yield 扣留的错误事件 → return
                if withheld_error is not None:
                    yield withheld_error
                yield LoopResult(reason="prompt_too_long", error=error_occurred)
                return

        # 7b. finish_reason=length → 恢复消息 → continue（最多 3 次）
        if finish_reason == "length":
            if state.max_output_tokens_recovery_count < MAX_OUTPUT_TOKENS_RECOVERY_LIMIT:
                recovery_msg = {
                    "role": "user",
                    "content": (
                        "Output token limit hit. Resume directly — no apology, "
                        "no recap of what you were doing. Pick up mid-thought "
                        "if that is where the cut happened. Break remaining "
                        "work into smaller pieces."
                    ),
                }
                next_messages = [*messages, assistant_msg, recovery_msg]
                engine.mutable_messages = next_messages
                messages = next_messages
                updates = {
                    "max_output_tokens_recovery_count": state.max_output_tokens_recovery_count + 1,
                    "transition": "max_output_tokens_recovery",
                }
                state = State(**{**asdict(state), **updates})
                continue

            # 恢复次数用尽
            yield StreamEvent(
                type="error",
                error=RuntimeError("Max output tokens recovery limit exceeded"),
                content="Output token limit recovery exhausted after 3 attempts",
            )
            yield LoopResult(reason="max_output_tokens_exhausted")
            return

        # 7c. 其他错误 → yield error → return
        if error_occurred:
            # 上下文超限但 is_recoverable_error 返回 False 时，
            # 7a 没进 if，错误事件已在流式循环里被扣下，这里 yield 出来
            if withheld_error is not None:
                yield withheld_error
            yield StreamEvent(
                type="error",
                error=error_occurred,
                content=str(error_occurred),
            )
            yield LoopResult(reason="model_error", error=error_occurred)
            return

        # 把 assistant 消息持久化到引擎，并更新局部 messages
        engine.mutable_messages = [*messages, assistant_msg]
        messages = [*messages, assistant_msg]

        # yield assistant 消息，让 REPL append 到自己的历史。
        # 放在错误恢复之后、完成检查之前——错误恢复走 continue/return 时
        # 不 yield（消息可能不完整或要重试），只有正常流程才 yield。
        yield assistant_msg

        # ---- 8. 完成检查 ----

        # 8a. finish_reason=stop → yield done → return
        if finish_reason == "stop":
            yield StreamEvent(type="done", finish_reason="stop")
            yield LoopResult(reason="completed")
            return

        # 8b. 无工具调用 → yield done → return
        if not tool_calls:
            # 运行停止钩子
            stop_result = await run_stop_hooks(messages)
            if stop_result.should_stop:
                yield StreamEvent(type="done", finish_reason="stop")
                yield LoopResult(reason="completed")
                return

            yield StreamEvent(type="done", finish_reason="stop")
            yield LoopResult(reason="completed")
            return

        # ---- 9. 执行工具 ----
        from tools.protocol import ToolUseContext

        tool_use_context = ToolUseContext()

        try:
            tool_results = await deps.execute_tools(
                tool_calls=tool_calls,
                tools=engine_config.tools,
                context=tool_use_context,
            )
        except Exception as e:
            yield StreamEvent(
                type="error",
                error=e,
                content=f"Tool execution error: {e}",
            )
            yield LoopResult(reason="tool_error", error=e)
            return

        # ---- 10. 追加工具结果 ----
        tool_result_messages = _build_tool_result_messages(tool_results)

        # yield 工具结果
        for tr_msg in tool_result_messages:
            yield tr_msg

        # ---- 11. 状态转换 ----
        next_messages = [*messages, *tool_result_messages]
        engine.mutable_messages = next_messages

        updates = {
            "max_output_tokens_recovery_count": 0,
            "transition": "next_turn",
        }
        state = State(**{**asdict(state), **updates})

        # 更新压缩追踪
        tracking.consecutive_failures = 0


# ---------------------------------------------------------------------------
# 测试块
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import asyncio

    from query.deps import QueryDeps
    from query.engine import QueryEngine, QueryEngineConfig

    print("=" * 60)
    print("Agentic 循环引擎测试")
    print("=" * 60)

    # ---- 测试 1: State 创建（瘦身后字段）----
    print("\n--- 测试 1: State 创建（瘦身后字段）---")
    state = State()
    assert state.transition is None
    assert state.error_count == 0
    assert state.withheld_messages == []
    assert state.max_output_tokens_recovery_count == 0
    assert state.has_attempted_reactive_compact is False
    assert state.auto_compact_tracking is None
    print(f"  transition={state.transition}, error_count={state.error_count}")

    # 状态转换（只含临时状态字段）
    updates = {
        "transition": "next_turn",
        "max_output_tokens_recovery_count": 1,
    }
    state = State(**{**asdict(state), **updates})
    assert state.transition == "next_turn"
    assert state.max_output_tokens_recovery_count == 1
    print(f"  转换后: transition={state.transition}, "
          f"recovery_count={state.max_output_tokens_recovery_count}")
    print("  [PASS] State 创建（瘦身后字段）")

    # ---- 测试 2: _is_context_length_error ----
    print("\n--- 测试 2: _is_context_length_error ---")
    assert _is_context_length_error(RuntimeError("context_length_exceeded")) is True
    assert _is_context_length_error(RuntimeError("maximum context length exceeded")) is True
    assert _is_context_length_error(RuntimeError("prompt too long")) is True
    assert _is_context_length_error(RuntimeError("rate limit")) is False
    print("  [PASS] _is_context_length_error")

    # ---- 测试 3: _build_assistant_message ----
    print("\n--- 测试 3: _build_assistant_message ---")
    msg = _build_assistant_message(["Hello", " world"], [])
    assert msg["role"] == "assistant"
    assert msg["content"] == "Hello world"
    assert "tool_calls" not in msg

    msg_with_tools = _build_assistant_message(
        ["Using tool..."],
        [{"id": "call_001", "function": {"name": "read", "arguments": "{}"}}],
    )
    assert msg_with_tools["content"] == "Using tool..."
    assert len(msg_with_tools["tool_calls"]) == 1

    msg_empty = _build_assistant_message([], [])
    assert msg_empty["role"] == "assistant"
    print("  [PASS] _build_assistant_message")

    # ---- 测试 4: _build_tool_result_messages ----
    print("\n--- 测试 4: _build_tool_result_messages ---")
    results = [
        ToolExecutionResult(tool_call_id="c1", tool_name="read", content="file content"),
        ToolExecutionResult(tool_call_id="c2", tool_name="write", content="ok", is_error=True),
    ]
    msgs = _build_tool_result_messages(results)
    assert len(msgs) == 2
    assert msgs[0]["role"] == "tool"
    assert msgs[0]["tool_call_id"] == "c1"
    assert msgs[0]["content"] == "file content"
    assert msgs[1]["tool_call_id"] == "c2"
    print("  [PASS] _build_tool_result_messages")

    # ---- 测试 5: query_loop 使用 mock deps 的基本流程 ----
    print("\n--- 测试 5: query_loop 使用 mock deps 的基本流程 ---")

    async def _test_query_loop():
        # 构造 mock deps
        async def mock_call_model(**kwargs):
            # 模拟一个简单的 stop 响应
            yield StreamEvent(type="content", content="Hello!")
            yield StreamEvent(type="done", finish_reason="stop")

        def mock_microcompact(messages=None, **kwargs):
            return messages

        async def mock_autocompact(**kwargs):
            return kwargs.get("messages", []), False

        async def mock_execute_tools(**kwargs):
            return []

        mock_deps = QueryDeps(
            call_model=mock_call_model,
            microcompact=mock_microcompact,
            autocompact=mock_autocompact,
            execute_tools=mock_execute_tools,
            get_uuid=lambda: "test-uuid",
        )

        engine_config = QueryEngineConfig(
            model="test-model", max_tokens=4096, deps=mock_deps,
        )
        engine = QueryEngine(
            engine_config,
            initial_messages=[{"role": "user", "content": "Hi"}],
        )
        config = build_query_config()

        events = []
        async for event in query_loop(engine, config):
            events.append(event)

        # 验证至少收到了 content 和 done 事件
        content_events = [e for e in events if isinstance(e, StreamEvent) and e.type == "content"]
        done_events = [e for e in events if isinstance(e, StreamEvent) and e.type == "done"]
        # yield 合约：正常 stop 流程会额外 yield assistant 消息（dict）
        assistant_msgs = [e for e in events if isinstance(e, dict) and e.get("role") == "assistant"]

        assert len(content_events) > 0, f"期望至少 1 个 content 事件, 得到 {len(content_events)}"
        assert len(done_events) > 0, f"期望至少 1 个 done 事件, 得到 {len(done_events)}"
        assert done_events[0].finish_reason == "stop"
        assert len(assistant_msgs) == 1, f"期望 1 条 assistant 消息, 得到 {len(assistant_msgs)}"
        assert assistant_msgs[0]["content"] == "Hello!"

        # 验证 LoopResult
        loop_results = [e for e in events if isinstance(e, LoopResult)]
        assert len(loop_results) > 0
        assert loop_results[-1].reason == "completed"

        # 验证引擎消息已更新
        assert len(engine.mutable_messages) == 2  # user + assistant
        assert engine.mutable_messages[1]["content"] == "Hello!"

        print(f"  收到 {len(events)} 个事件")
        print(f"  content 事件: {len(content_events)}, done 事件: {len(done_events)}")
        print(f"  assistant 消息: {len(assistant_msgs)}")
        print(f"  引擎消息: {len(engine.mutable_messages)} 条")

    asyncio.run(_test_query_loop())
    print("  [PASS] query_loop 使用 mock deps 的基本流程")

    # ---- 测试 6: query_loop 工具调用流程 ----
    print("\n--- 测试 6: query_loop 工具调用流程 ---")

    async def _test_tool_call_loop():
        call_count = 0

        async def mock_call_model_with_tool(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # 第一次：返回工具调用
                yield StreamEvent(type="content", content="Let me check.")
                yield StreamEvent(
                    type="tool_call_delta",
                    tool_call_id="call_001",
                    tool_call_name="read_file",
                    tool_call_arguments='{"path": "/tmp/test.py"}',
                )
                yield StreamEvent(type="done", finish_reason="tool_calls")
            else:
                # 第二次：返回最终响应
                yield StreamEvent(type="content", content="The file contains Python code.")
                yield StreamEvent(type="done", finish_reason="stop")

        def mock_microcompact(messages=None, **kwargs):
            return messages

        async def mock_autocompact(**kwargs):
            return kwargs.get("messages", []), False

        async def mock_execute_tools(**kwargs):
            return [
                ToolExecutionResult(
                    tool_call_id="call_001",
                    tool_name="read_file",
                    content="print('hello')",
                )
            ]

        mock_deps = QueryDeps(
            call_model=mock_call_model_with_tool,
            microcompact=mock_microcompact,
            autocompact=mock_autocompact,
            execute_tools=mock_execute_tools,
            get_uuid=lambda: "test-uuid",
        )

        engine_config = QueryEngineConfig(
            model="test-model", max_tokens=4096, deps=mock_deps,
        )
        engine = QueryEngine(
            engine_config,
            initial_messages=[{"role": "user", "content": "Read the file"}],
        )
        config = build_query_config()

        events = []
        async for event in query_loop(engine, config):
            events.append(event)

        # 验证收到了工具结果和最终 done
        done_events = [e for e in events if isinstance(e, StreamEvent) and e.type == "done"]
        tool_result_msgs = [e for e in events if isinstance(e, dict) and e.get("role") == "tool"]
        # yield 合约：两轮各 yield 一条 assistant 消息
        assistant_msgs = [e for e in events if isinstance(e, dict) and e.get("role") == "assistant"]

        assert len(done_events) > 0
        assert len(tool_result_msgs) > 0
        assert len(assistant_msgs) == 2, f"期望 2 条 assistant 消息, 得到 {len(assistant_msgs)}"
        assert call_count == 2  # 第一次工具调用 + 第二次最终响应

        # 验证最后一轮的 LoopResult
        loop_results = [e for e in events if isinstance(e, LoopResult)]
        assert len(loop_results) > 0
        assert loop_results[-1].reason == "completed"

        print(f"  总事件: {len(events)}, done: {len(done_events)}, tool_results: {len(tool_result_msgs)}")
        print(f"  assistant 消息: {len(assistant_msgs)}")
        print(f"  模型调用次数: {call_count}")
        print(f"  引擎消息: {len(engine.mutable_messages)} 条")

    asyncio.run(_test_tool_call_loop())
    print("  [PASS] query_loop 工具调用流程")

    # ---- 测试 7: query 入口函数 ----
    print("\n--- 测试 7: query 入口函数 ---")

    async def _test_query_entry():
        async def mock_call_model(**kwargs):
            yield StreamEvent(type="content", content="Response")
            yield StreamEvent(type="done", finish_reason="stop")

        def mock_microcompact(messages=None, **kwargs):
            return messages

        async def mock_autocompact(**kwargs):
            return kwargs.get("messages", []), False

        async def mock_execute_tools(**kwargs):
            return []

        mock_deps = QueryDeps(
            call_model=mock_call_model,
            microcompact=mock_microcompact,
            autocompact=mock_autocompact,
            execute_tools=mock_execute_tools,
        )

        events = []
        async for event in query({
            "messages": [{"role": "user", "content": "Hello"}],
            "config_overrides": {"deps": mock_deps, "model": "test-model", "max_tokens": 4096},
        }):
            events.append(event)

        assert len(events) > 0
        done_events = [e for e in events if isinstance(e, StreamEvent) and e.type == "done"]
        assert len(done_events) > 0
        # 验证 LoopResult
        loop_results = [e for e in events if isinstance(e, LoopResult)]
        assert len(loop_results) > 0
        assert loop_results[-1].reason == "completed"
        print(f"  收到 {len(events)} 个事件")

    asyncio.run(_test_query_entry())
    print("  [PASS] query 入口函数")

    # ---- 测试 8: query 传入 user_context / system_context ----
    print("\n--- 测试 8: query 传入 user_context / system_context ---")

    async def _test_query_with_context():
        async def mock_call_model(**kwargs):
            yield StreamEvent(type="content", content="OK")
            yield StreamEvent(type="done", finish_reason="stop")

        def mock_microcompact(messages=None, **kwargs):
            return messages

        async def mock_autocompact(**kwargs):
            return kwargs.get("messages", []), False

        async def mock_execute_tools(**kwargs):
            return []

        mock_deps = QueryDeps(
            call_model=mock_call_model,
            microcompact=mock_microcompact,
            autocompact=mock_autocompact,
            execute_tools=mock_execute_tools,
        )

        events = []
        async for event in query({
            "messages": [{"role": "user", "content": "Hello"}],
            "config_overrides": {"deps": mock_deps},
            "user_context": {"currentDate": "今日日期是 2026年06月19日"},
            "system_context": {"gitStatus": "clean"},
        }):
            events.append(event)

        # 验证不报错且正常收到事件
        assert len(events) > 0
        done_events = [e for e in events if isinstance(e, StreamEvent) and e.type == "done"]
        assert len(done_events) > 0
        assert done_events[0].finish_reason == "stop"
        # 验证 LoopResult
        loop_results = [e for e in events if isinstance(e, LoopResult)]
        assert len(loop_results) > 0
        assert loop_results[-1].reason == "completed"
        print(f"  收到 {len(events)} 个事件，context 注入正常")

    asyncio.run(_test_query_with_context())
    print("  [PASS] query 传入 user_context / system_context")

    # ---- 测试 9: QueryConfig 只有 3 个字段 ----
    print("\n--- 测试 9: QueryConfig 只有 3 个字段 ---")
    import dataclasses

    field_names = {f.name for f in dataclasses.fields(QueryConfig)}
    assert field_names == {"session_id", "auto_compact_enabled", "context_collapse_enabled"}, \
        f"期望 3 个字段, 得到 {field_names}"
    print(f"  QueryConfig 字段: {field_names}")
    print("  [PASS] QueryConfig 只有 3 个字段")

    # ---- 测试 10: _run_inline_compression 无压缩时返回原消息 ----
    print("\n--- 测试 10: _run_inline_compression 无压缩时返回原消息 ---")

    async def _test_inline_compression_noop():
        async def _unused_call_model(**kwargs):
            if False:  # pragma: no cover
                yield  # 占位，让函数成为 async generator

        def mock_microcompact(messages=None, **kwargs):
            return messages

        async def mock_autocompact(**kwargs):
            return kwargs.get("messages", []), False

        async def mock_execute_tools(**kwargs):
            return []

        mock_deps = QueryDeps(
            call_model=_unused_call_model,
            microcompact=mock_microcompact,
            autocompact=mock_autocompact,
            execute_tools=mock_execute_tools,
            get_uuid=lambda: "test-uuid",
        )

        # 小消息：snip 未门控、无 assistant 时间戳、context_collapse 关闭、
        # autocompact 阈值未达 → 各级都不触发，返回原消息
        messages = [{"role": "user", "content": "Hi"}]
        tracking = CompactTracking()
        result = await _run_inline_compression(
            messages=messages,
            model="test-model",
            tracking=tracking,
            context_collapse_enabled=False,
            deps=mock_deps,
        )
        assert result == messages, f"期望原消息, 得到 {result}"
        print(f"  输入 {len(messages)} 条, 输出 {len(result)} 条, 无压缩触发")
        print("  [PASS] _run_inline_compression 无压缩时返回原消息")

    asyncio.run(_test_inline_compression_noop())

    # ---- 测试 11: withhold 恢复成功 ----
    print("\n--- 测试 11: withhold 恢复成功 ---")

    async def _test_withhold_recovery_success():
        import httpx
        import openai

        call_count = 0
        # autocompact 调用计数：区分压缩管线调用和恢复路径调用
        compact_call_count = 0

        def _make_response(status_code: int = 400) -> httpx.Response:
            """构造 openai SDK 错误需要的 httpx.Response。"""
            return httpx.Response(
                status_code=status_code,
                request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions"),
            )

        async def mock_call_model_withhold(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # 第一次：返回上下文超限错误
                # 用 BadRequestError 以便 classify_error 识别为可恢复的
                # context_length_exceeded 类型
                err = openai.BadRequestError(
                    message="context_length_exceeded: maximum context length exceeded",
                    response=_make_response(400),
                    body=None,
                )
                yield StreamEvent(type="error", error=err, content="context_length_exceeded")
            else:
                # 第二次：返回正常响应
                yield StreamEvent(type="content", content="Recovered!")
                yield StreamEvent(type="done", finish_reason="stop")

        # mock autocompact：压缩管线调用时不压缩，
        # 恢复路径调用时返回缩减后的消息（模拟压缩成功）
        async def mock_autocompact_withhold(**kwargs):
            nonlocal compact_call_count
            compact_call_count += 1
            msgs = kwargs.get("messages", [])
            if compact_call_count == 2:
                # 恢复路径调用：返回缩减后的消息（去掉第一条），模拟压缩成功
                return msgs[1:] if len(msgs) > 1 else msgs, True
            # 压缩管线调用：不压缩
            return msgs, False

        def mock_microcompact_withhold(messages=None, **kwargs):
            return messages

        async def mock_execute_tools_withhold(**kwargs):
            return []

        mock_deps = QueryDeps(
            call_model=mock_call_model_withhold,
            microcompact=mock_microcompact_withhold,
            autocompact=mock_autocompact_withhold,
            execute_tools=mock_execute_tools_withhold,
            get_uuid=lambda: "test-uuid",
        )

        engine_config = QueryEngineConfig(
            model="test-model", max_tokens=4096, deps=mock_deps,
        )
        engine = QueryEngine(
            engine_config,
            initial_messages=[
                {"role": "user", "content": "msg1"},
                {"role": "user", "content": "msg2"},
            ],
        )
        config = build_query_config()

        events = []
        async for event in query_loop(engine, config):
            events.append(event)

        # 验证：error 事件不应该出现在 events 里（被 withhold 了）
        error_events = [e for e in events if isinstance(e, StreamEvent) and e.type == "error"]
        assert len(error_events) == 0, f"期望 0 个 error 事件（应被 withhold），得到 {len(error_events)}"

        # 验证：模型被调用了 2 次（第一次报错，第二次恢复）
        assert call_count == 2, f"期望调用 2 次，得到 {call_count}"

        # 验证：正常收到了 content 和 LoopResult
        content_events = [e for e in events if isinstance(e, StreamEvent) and e.type == "content" and e.content]
        assert len(content_events) > 0
        assert content_events[0].content == "Recovered!"

        loop_results = [e for e in events if isinstance(e, LoopResult)]
        assert len(loop_results) > 0
        assert loop_results[-1].reason == "completed"

        print(f"  模型调用次数: {call_count}")
        print(f"  error 事件: {len(error_events)}（被 withhold）")
        print(f"  LoopResult: {loop_results[-1].reason}")

    asyncio.run(_test_withhold_recovery_success())
    print("  [PASS] withhold 恢复成功")

    # ---- 测试 12: withhold 恢复失败 ----
    print("\n--- 测试 12: withhold 恢复失败 ---")

    async def _test_withhold_recovery_fail():
        import httpx
        import openai

        def _make_response(status_code: int = 400) -> httpx.Response:
            """构造 openai SDK 错误需要的 httpx.Response。"""
            return httpx.Response(
                status_code=status_code,
                request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions"),
            )

        async def mock_call_model_fail(**kwargs):
            # 用 BadRequestError 以便走恢复路径，但 autocompact 压缩无效
            err = openai.BadRequestError(
                message="context_length_exceeded: maximum context length exceeded",
                response=_make_response(400),
                body=None,
            )
            yield StreamEvent(type="error", error=err, content="context_length_exceeded")

        # mock autocompact 返回原消息（压缩无效）
        async def mock_autocompact_fail(**kwargs):
            return kwargs.get("messages", []), False

        def mock_microcompact_fail(messages=None, **kwargs):
            return messages

        async def mock_execute_tools_fail(**kwargs):
            return []

        mock_deps = QueryDeps(
            call_model=mock_call_model_fail,
            microcompact=mock_microcompact_fail,
            autocompact=mock_autocompact_fail,
            execute_tools=mock_execute_tools_fail,
            get_uuid=lambda: "test-uuid",
        )

        engine_config = QueryEngineConfig(
            model="test-model", max_tokens=4096, deps=mock_deps,
        )
        engine = QueryEngine(
            engine_config,
            initial_messages=[{"role": "user", "content": "Hi"}],
        )
        config = build_query_config()

        events = []
        async for event in query_loop(engine, config):
            events.append(event)

        # 验证：error 事件最终被 yield（恢复失败后暴露）
        error_events = [e for e in events if isinstance(e, StreamEvent) and e.type == "error"]
        assert len(error_events) > 0, "期望至少 1 个 error 事件（恢复失败后暴露）"

        # 验证：LoopResult reason 是 prompt_too_long
        loop_results = [e for e in events if isinstance(e, LoopResult)]
        assert len(loop_results) > 0
        assert loop_results[-1].reason == "prompt_too_long", f"期望 prompt_too_long，得到 {loop_results[-1].reason}"

        print(f"  error 事件: {len(error_events)}（恢复失败后暴露）")
        print(f"  LoopResult: {loop_results[-1].reason}")

    asyncio.run(_test_withhold_recovery_fail())
    print("  [PASS] withhold 恢复失败")

    # ---- 测试 13: 不可恢复错误不扣留 ----
    print("\n--- 测试 13: 不可恢复错误不扣留 ---")

    async def _test_non_recoverable_error():
        async def mock_call_model_auth_error(**kwargs):
            yield StreamEvent(type="error", error=RuntimeError("Invalid API key"), content="Invalid API key")

        def mock_microcompact_none(messages=None, **kwargs):
            return messages

        async def mock_autocompact_none(**kwargs):
            return kwargs.get("messages", []), False

        async def mock_execute_tools_none(**kwargs):
            return []

        mock_deps = QueryDeps(
            call_model=mock_call_model_auth_error,
            microcompact=mock_microcompact_none,
            autocompact=mock_autocompact_none,
            execute_tools=mock_execute_tools_none,
            get_uuid=lambda: "test-uuid",
        )

        engine_config = QueryEngineConfig(
            model="test-model", max_tokens=4096, deps=mock_deps,
        )
        engine = QueryEngine(
            engine_config,
            initial_messages=[{"role": "user", "content": "Hi"}],
        )
        config = build_query_config()

        events = []
        async for event in query_loop(engine, config):
            events.append(event)

        # 验证：error 事件立即 yield（不可恢复错误不扣留）
        error_events = [e for e in events if isinstance(e, StreamEvent) and e.type == "error"]
        assert len(error_events) > 0, "期望 error 事件立即 yield"

        # 验证：LoopResult reason 是 model_error
        loop_results = [e for e in events if isinstance(e, LoopResult)]
        assert len(loop_results) > 0
        assert loop_results[-1].reason == "model_error", f"期望 model_error，得到 {loop_results[-1].reason}"

        print(f"  error 事件: {len(error_events)}（立即 yield，不扣留）")
        print(f"  LoopResult: {loop_results[-1].reason}")

    asyncio.run(_test_non_recoverable_error())
    print("  [PASS] 不可恢复错误不扣留")

    # ---- 测试 14: tool_error — 工具执行异常退出 ----
    print("\n--- 测试 14: tool_error — 工具执行异常退出 ---")

    async def _test_tool_error():
        async def mock_call_model_tool_error(**kwargs):
            # 返回工具调用，让循环走到工具执行步骤
            yield StreamEvent(type="content", content="Let me run a tool.")
            yield StreamEvent(
                type="tool_call_delta",
                tool_call_id="call_001",
                tool_call_name="read_file",
                tool_call_arguments='{"path": "/tmp/test.py"}',
            )
            yield StreamEvent(type="done", finish_reason="tool_calls")

        def mock_microcompact_tool_error(messages=None, **kwargs):
            return messages

        async def mock_autocompact_tool_error(**kwargs):
            return kwargs.get("messages", []), False

        async def mock_execute_tools_error(**kwargs):
            # 工具执行抛异常，触发 tool_error 退出路径
            raise RuntimeError("Tool execution failed")

        mock_deps = QueryDeps(
            call_model=mock_call_model_tool_error,
            microcompact=mock_microcompact_tool_error,
            autocompact=mock_autocompact_tool_error,
            execute_tools=mock_execute_tools_error,
            get_uuid=lambda: "test-uuid",
        )

        engine_config = QueryEngineConfig(
            model="test-model", max_tokens=4096, deps=mock_deps,
        )
        engine = QueryEngine(
            engine_config,
            initial_messages=[{"role": "user", "content": "Read the file"}],
        )
        config = build_query_config()

        events = []
        async for event in query_loop(engine, config):
            events.append(event)

        # 验证：有 error 事件，content 含 "Tool execution error"
        error_events = [e for e in events if isinstance(e, StreamEvent) and e.type == "error"]
        assert len(error_events) > 0, "期望至少 1 个 error 事件"
        assert "Tool execution error" in error_events[-1].content, \
            f"期望 content 含 'Tool execution error', 得到 {error_events[-1].content!r}"

        # 验证：LoopResult reason 是 tool_error，error 是 RuntimeError
        loop_results = [e for e in events if isinstance(e, LoopResult)]
        assert len(loop_results) > 0
        assert loop_results[-1].reason == "tool_error", \
            f"期望 tool_error，得到 {loop_results[-1].reason}"
        assert isinstance(loop_results[-1].error, RuntimeError), \
            f"期望 RuntimeError，得到 {type(loop_results[-1].error).__name__}"

        print(f"  error 事件: {len(error_events)}")
        print(f"  error content: {error_events[-1].content}")
        print(f"  LoopResult: {loop_results[-1].reason}")
        print(f"  error 类型: {type(loop_results[-1].error).__name__}")

    asyncio.run(_test_tool_error())
    print("  [PASS] tool_error — 工具执行异常退出")

    # ---- 测试 15: max_output_tokens_exhausted — 输出 token 恢复次数用尽 ----
    print("\n--- 测试 15: max_output_tokens_exhausted — 输出 token 恢复次数用尽 ---")

    async def _test_max_output_tokens_exhausted():
        call_count = 0

        async def mock_call_model_length(**kwargs):
            # 每次都返回 finish_reason="length"，永远触发恢复
            nonlocal call_count
            call_count += 1
            yield StreamEvent(type="content", content="partial")
            yield StreamEvent(type="done", finish_reason="length")

        def mock_microcompact_length(messages=None, **kwargs):
            return messages

        async def mock_autocompact_length(**kwargs):
            return kwargs.get("messages", []), False

        async def mock_execute_tools_length(**kwargs):
            return []

        mock_deps = QueryDeps(
            call_model=mock_call_model_length,
            microcompact=mock_microcompact_length,
            autocompact=mock_autocompact_length,
            execute_tools=mock_execute_tools_length,
            get_uuid=lambda: "test-uuid",
        )

        engine_config = QueryEngineConfig(
            model="test-model", max_tokens=4096, deps=mock_deps,
        )
        engine = QueryEngine(
            engine_config,
            initial_messages=[{"role": "user", "content": "Hi"}],
        )
        config = build_query_config()

        events = []
        async for event in query_loop(engine, config):
            events.append(event)

        # 验证：模型调用次数 == MAX_OUTPUT_TOKENS_RECOVERY_LIMIT + 1
        # 初始 1 次 + 3 次恢复，第 4 次恢复次数用尽退出
        assert call_count == MAX_OUTPUT_TOKENS_RECOVERY_LIMIT + 1, \
            f"期望 {MAX_OUTPUT_TOKENS_RECOVERY_LIMIT + 1} 次，得到 {call_count}"

        # 验证：LoopResult reason 是 max_output_tokens_exhausted
        loop_results = [e for e in events if isinstance(e, LoopResult)]
        assert len(loop_results) > 0
        assert loop_results[-1].reason == "max_output_tokens_exhausted", \
            f"期望 max_output_tokens_exhausted，得到 {loop_results[-1].reason}"

        print(f"  模型调用次数: {call_count}")
        print(f"  LoopResult: {loop_results[-1].reason}")

    asyncio.run(_test_max_output_tokens_exhausted())
    print("  [PASS] max_output_tokens_exhausted — 输出 token 恢复次数用尽")

    print("\n" + "=" * 60)
    print("所有测试完成")
    print("=" * 60)
