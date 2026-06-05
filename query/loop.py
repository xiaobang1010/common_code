"""Agentic 循环引擎核心。

参考原始 TypeScript 实现 src/query.ts。

核心查询入口和 while(true) 无限循环，
每轮迭代执行：压缩 → 构建请求 → 调用模型 → 流式输出 →
工具调用 → 追加结果 → 错误恢复 → 完成检查 → 状态转换。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, AsyncGenerator

from query.config import QueryConfig, build_query_config
from query.deps import QueryDeps, production_deps
from query.stop_hooks import StopHookResult, run_stop_hooks
from query.token_budget import TokenBudget, estimate_tokens, is_over_budget, remaining
from query.services.api.errors import APIError, classify_error, is_recoverable_error
from query.services.api.llm import StreamEvent, collect_tool_calls
from query.services.compact.auto_compact import CompactTracking
from tools.executor import (
    ToolExecutionResult,
    tool_result_to_openai_message,
)
from query.utils.api import build_api_request


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# max_output_tokens 恢复重试上限
MAX_OUTPUT_TOKENS_RECOVERY_LIMIT = 3

# 上下文长度超限时的错误关键词
_CONTEXT_LENGTH_KEYWORDS = ("context_length", "maximum context length", "prompt too long")


# ---------------------------------------------------------------------------
# State — 跨迭代状态
# ---------------------------------------------------------------------------


@dataclass
class State:
    """跨迭代可变状态。

    每次循环迭代开始时解构，continue 时整体赋值：
      state = State(**{**asdict(state), **updates})

    Attributes:
        messages: 消息列表
        turn_count: 当前轮次
        transition: 转换原因（防止死循环）
        total_tokens_used: 累计 token 使用
        error_count: 错误计数
        withheld_messages: 被暂扣的消息（可恢复错误恢复前暂不输出）
        max_output_tokens_recovery_count: max_output_tokens 恢复计数
        has_attempted_reactive_compact: 是否已尝试响应式压缩
        auto_compact_tracking: 自动压缩追踪状态
    """

    messages: list[dict] = field(default_factory=list)
    turn_count: int = 1
    transition: str | None = None
    total_tokens_used: int = 0
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
# query — 核心查询入口
# ---------------------------------------------------------------------------


async def query(
    params: dict[str, Any] | None = None,
) -> AsyncGenerator[StreamEvent | dict, None]:
    """核心查询入口。

    构建 QueryConfig 和 QueryDeps，调用 query_loop。

    Args:
        params: 查询参数，支持以下字段：
            - messages: 初始消息列表
            - config_overrides: QueryConfig 覆盖字段
            - deps: 自定义依赖（None 时使用 production_deps）

    Yields:
        StreamEvent | dict: 流式事件或结果消息
    """
    if params is None:
        params = {}

    messages = params.get("messages", [])
    config_overrides = params.get("config_overrides", {})
    deps = params.get("deps") or production_deps()

    config = build_query_config(**config_overrides)

    state = State(messages=messages)

    async for event in query_loop(state, config, deps):
        yield event


# ---------------------------------------------------------------------------
# query_loop — while(true) 无限循环
# ---------------------------------------------------------------------------


async def query_loop(
    state: State,
    config: QueryConfig,
    deps: QueryDeps,
) -> AsyncGenerator[StreamEvent | dict, None]:
    """Agentic 循环核心。

    while(true) 无限循环，每轮迭代：
    1. 压缩管线
    2. 构建 API 请求
    3. 调用模型（流式）
    4. 流式输出
    5. 检测工具调用
    6. 执行工具
    7. 追加结果到消息列表
    8. 错误恢复
    9. 完成检查
    10. 状态转换

    Args:
        state: 初始状态
        config: 不可变配置
        deps: I/O 依赖

    Yields:
        StreamEvent | dict: 流式事件或结果消息
    """
    # 初始化压缩追踪
    tracking: CompactTracking = CompactTracking()

    # Token 预算
    budget = TokenBudget(
        total=128000,
        reserved=config.max_tokens,
    )

    # eslint-disable-next-line no-constant-condition
    while True:
        # 解构状态
        messages = state.messages
        turn_count = state.turn_count
        transition = state.transition

        # ---- 1. 压缩管线 ----
        if config.auto_compact_enabled and messages:
            try:
                compacted = await deps.compact(
                    messages=messages,
                    model=config.model,
                    tracking=tracking,
                    context_collapse_enabled=config.context_collapse_enabled,
                )
                if compacted is not None and compacted != messages:
                    # 压缩后替换消息
                    messages = compacted
                    # 重置追踪
                    tracking = CompactTracking()
            except Exception:
                # 压缩失败不中断循环
                pass

        # ---- 2. Token 预算检查 ----
        budget.used = estimate_tokens(messages)
        if is_over_budget(budget):
            yield StreamEvent(
                type="error",
                error=RuntimeError("Token budget exceeded"),
                content="Token budget exceeded: cannot fit within context window",
            )
            return

        # ---- 3. 构建 API 请求 ----
        from startup.constants.prompts import build_system_messages, get_system_prompt_sections

        sections = config.system_prompt_sections or get_system_prompt_sections()
        system_messages = build_system_messages(sections)

        request = build_api_request(
            messages=messages,
            system_prompt=system_messages,
            tools=config.tools,
            model=config.model,
            max_tokens=config.max_tokens,
            temperature=config.temperature,
        )

        # ---- 4. 调用模型（流式） ----
        yield StreamEvent(type="content", content="")  # stream_request_start 信号

        content_parts: list[str] = []
        stream_events: list[StreamEvent] = []
        finish_reason: str | None = None
        usage_info: dict | None = None
        error_occurred: Exception | None = None

        try:
            async for event in deps.call_model(
                messages=request["messages"],
                tools=config.tools,
                model=config.model,
                max_tokens=config.max_tokens,
                temperature=config.temperature,
            ):
                # ---- 5. 流式输出 ----
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
            return

        # 更新 token 使用量
        if usage_info:
            state.total_tokens_used += usage_info.get("total_tokens", 0)

        # ---- 6. 检测工具调用 ----
        tool_calls = collect_tool_calls(stream_events)

        # 构建 assistant 消息
        assistant_msg = _build_assistant_message(content_parts, tool_calls)

        # ---- 8. 错误恢复 ----

        # 8a. 上下文长度超限 → 尝试压缩恢复
        if error_occurred and _is_context_length_error(error_occurred):
            api_error = classify_error(error_occurred)
            if is_recoverable_error(api_error):
                # 尝试压缩恢复
                if not state.has_attempted_reactive_compact and config.auto_compact_enabled:
                    try:
                        compacted = await deps.compact(
                            messages=messages,
                            model=config.model,
                            tracking=tracking,
                            context_collapse_enabled=config.context_collapse_enabled,
                        )
                        if compacted is not None and compacted != messages:
                            updates = {
                                "messages": compacted,
                                "has_attempted_reactive_compact": True,
                                "transition": "reactive_compact_retry",
                            }
                            state = State(**{**asdict(state), **updates})
                            continue
                    except Exception:
                        pass

                # 压缩恢复失败 → yield error → return
                yield StreamEvent(
                    type="error",
                    error=error_occurred,
                    content="Context length exceeded and recovery failed",
                )
                return

        # 8b. finish_reason=length → 恢复消息 → continue（最多 3 次）
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
                updates = {
                    "messages": [*messages, assistant_msg, recovery_msg],
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
            return

        # 8c. 其他错误 → yield error → return
        if error_occurred:
            yield StreamEvent(
                type="error",
                error=error_occurred,
                content=str(error_occurred),
            )
            return

        # ---- 9. 完成检查 ----

        # 9a. finish_reason=stop → yield done → return
        if finish_reason == "stop":
            yield StreamEvent(type="done", finish_reason="stop")
            return

        # 9b. 无工具调用 → yield done → return
        if not tool_calls:
            # 运行停止钩子
            all_messages = [*messages, assistant_msg]
            stop_result = await run_stop_hooks(all_messages)
            if stop_result.should_stop:
                yield StreamEvent(type="done", finish_reason="stop")
                return

            yield StreamEvent(type="done", finish_reason="stop")
            return

        # ---- 7. 执行工具 ----
        from tools.protocol import ToolUseContext

        tool_use_context = ToolUseContext()

        try:
            tool_results = await deps.execute_tools(
                tool_calls=tool_calls,
                tools=config.tools,
                context=tool_use_context,
            )
        except Exception as e:
            yield StreamEvent(
                type="error",
                error=e,
                content=f"Tool execution error: {e}",
            )
            return

        # 追加工具结果到消息
        tool_result_messages = _build_tool_result_messages(tool_results)

        # yield 工具结果
        for tr_msg in tool_result_messages:
            yield tr_msg

        # ---- 10. 状态转换 ----
        next_messages = [*messages, assistant_msg, *tool_result_messages]
        next_turn_count = turn_count + 1

        updates = {
            "messages": next_messages,
            "turn_count": next_turn_count,
            "max_output_tokens_recovery_count": 0,
            "has_attempted_reactive_compact": False,
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

    print("=" * 60)
    print("Agentic 循环引擎测试")
    print("=" * 60)

    # ---- 测试 1: State 创建和转换 ----
    print("\n--- 测试 1: State 创建和转换 ---")
    state = State(messages=[{"role": "user", "content": "hello"}], turn_count=1)
    assert state.messages == [{"role": "user", "content": "hello"}]
    assert state.turn_count == 1
    assert state.transition is None
    assert state.total_tokens_used == 0
    assert state.error_count == 0
    assert state.withheld_messages == []
    print(f"  初始: messages={len(state.messages)}, turn_count={state.turn_count}")

    # 状态转换
    updates = {
        "messages": [*state.messages, {"role": "assistant", "content": "hi"}],
        "turn_count": 2,
        "transition": "next_turn",
    }
    state = State(**{**asdict(state), **updates})
    assert len(state.messages) == 2
    assert state.turn_count == 2
    assert state.transition == "next_turn"
    print(f"  转换后: messages={len(state.messages)}, turn_count={state.turn_count}, "
          f"transition={state.transition}")
    print("  [PASS] State 创建和转换")

    # ---- 测试 2: QueryConfig 构建 ----
    print("\n--- 测试 2: QueryConfig 构建 ---")
    config = build_query_config()
    assert config.model == "gpt-4o" or config.model  # 可能被环境变量覆盖
    assert config.max_tokens > 0
    assert config.temperature > 0
    assert config.permission_mode in ("default", "plan", "auto", "bypass")
    print(f"  model={config.model}, max_tokens={config.max_tokens}, "
          f"temperature={config.temperature}, permission_mode={config.permission_mode}")

    config_custom = build_query_config(model="claude-3-5-sonnet", max_tokens=4096)
    assert config_custom.model == "claude-3-5-sonnet"
    assert config_custom.max_tokens == 4096
    print(f"  自定义: model={config_custom.model}, max_tokens={config_custom.max_tokens}")
    print("  [PASS] QueryConfig 构建")

    # ---- 测试 3: QueryDeps 工厂 ----
    print("\n--- 测试 3: QueryDeps 工厂 ---")
    deps = production_deps()
    assert deps.call_model is not None
    assert deps.compact is not None
    assert deps.execute_tools is not None
    assert deps.get_uuid is not None
    uuid1 = deps.get_uuid()
    uuid2 = deps.get_uuid()
    assert uuid1 != uuid2
    print(f"  call_model: {deps.call_model.__name__}")
    print(f"  compact: {deps.compact.__name__}")
    print(f"  execute_tools: {deps.execute_tools.__name__}")
    print(f"  uuid1={uuid1}, uuid2={uuid2}")
    print("  [PASS] QueryDeps 工厂")

    # ---- 测试 4: TokenBudget 计算 ----
    print("\n--- 测试 4: TokenBudget 计算 ---")
    budget = TokenBudget(used=50000, total=128000, reserved=8192)
    r = remaining(budget)
    assert r == 128000 - 50000 - 8192
    assert is_over_budget(budget) is False
    print(f"  remaining={r}, over_budget={is_over_budget(budget)}")

    budget_over = TokenBudget(used=200000, total=128000, reserved=8192)
    assert is_over_budget(budget_over) is True
    assert remaining(budget_over) == 0
    print(f"  over: remaining={remaining(budget_over)}, over_budget={is_over_budget(budget_over)}")

    tokens = estimate_tokens([{"role": "user", "content": "Hello world"}])
    assert tokens > 0
    print(f"  estimate_tokens('Hello world') ≈ {tokens}")
    print("  [PASS] TokenBudget 计算")

    # ---- 测试 5: query_loop 使用 mock deps 的基本流程 ----
    print("\n--- 测试 5: query_loop 使用 mock deps 的基本流程 ---")

    async def _test_query_loop():
        # 构造 mock deps
        async def mock_call_model(**kwargs):
            # 模拟一个简单的 stop 响应
            yield StreamEvent(type="content", content="Hello!")
            yield StreamEvent(type="done", finish_reason="stop")

        async def mock_compact(**kwargs):
            return kwargs.get("messages", [])

        async def mock_execute_tools(**kwargs):
            return []

        mock_deps = QueryDeps(
            call_model=mock_call_model,
            compact=mock_compact,
            execute_tools=mock_execute_tools,
            get_uuid=lambda: "test-uuid",
        )

        config = build_query_config(model="test-model", max_tokens=4096)
        state = State(messages=[{"role": "user", "content": "Hi"}])

        events = []
        async for event in query_loop(state, config, mock_deps):
            events.append(event)

        # 验证至少收到了 content 和 done 事件
        content_events = [e for e in events if isinstance(e, StreamEvent) and e.type == "content"]
        done_events = [e for e in events if isinstance(e, StreamEvent) and e.type == "done"]

        assert len(content_events) > 0, f"期望至少 1 个 content 事件, 得到 {len(content_events)}"
        assert len(done_events) > 0, f"期望至少 1 个 done 事件, 得到 {len(done_events)}"
        assert done_events[0].finish_reason == "stop"

        print(f"  收到 {len(events)} 个事件")
        print(f"  content 事件: {len(content_events)}, done 事件: {len(done_events)}")
        print(f"  finish_reason: {done_events[0].finish_reason}")

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

        async def mock_compact(**kwargs):
            return kwargs.get("messages", [])

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
            compact=mock_compact,
            execute_tools=mock_execute_tools,
            get_uuid=lambda: "test-uuid",
        )

        config = build_query_config(model="test-model", max_tokens=4096)
        state = State(messages=[{"role": "user", "content": "Read the file"}])

        events = []
        async for event in query_loop(state, config, mock_deps):
            events.append(event)

        # 验证收到了工具结果和最终 done
        done_events = [e for e in events if isinstance(e, StreamEvent) and e.type == "done"]
        tool_result_msgs = [e for e in events if isinstance(e, dict) and e.get("role") == "tool"]

        assert len(done_events) > 0
        assert len(tool_result_msgs) > 0
        assert call_count == 2  # 第一次工具调用 + 第二次最终响应

        print(f"  总事件: {len(events)}, done: {len(done_events)}, tool_results: {len(tool_result_msgs)}")
        print(f"  模型调用次数: {call_count}")

    asyncio.run(_test_tool_call_loop())
    print("  [PASS] query_loop 工具调用流程")

    # ---- 测试 7: _is_context_length_error ----
    print("\n--- 测试 7: _is_context_length_error ---")
    assert _is_context_length_error(RuntimeError("context_length_exceeded")) is True
    assert _is_context_length_error(RuntimeError("maximum context length exceeded")) is True
    assert _is_context_length_error(RuntimeError("prompt too long")) is True
    assert _is_context_length_error(RuntimeError("rate limit")) is False
    print("  [PASS] _is_context_length_error")

    # ---- 测试 8: _build_assistant_message ----
    print("\n--- 测试 8: _build_assistant_message ---")
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

    # ---- 测试 9: _build_tool_result_messages ----
    print("\n--- 测试 9: _build_tool_result_messages ---")
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

    # ---- 测试 10: query 入口函数 ----
    print("\n--- 测试 10: query 入口函数 ---")

    async def _test_query_entry():
        async def mock_call_model(**kwargs):
            yield StreamEvent(type="content", content="Response")
            yield StreamEvent(type="done", finish_reason="stop")

        async def mock_compact(**kwargs):
            return kwargs.get("messages", [])

        async def mock_execute_tools(**kwargs):
            return []

        mock_deps = QueryDeps(
            call_model=mock_call_model,
            compact=mock_compact,
            execute_tools=mock_execute_tools,
        )

        events = []
        async for event in query({
            "messages": [{"role": "user", "content": "Hello"}],
            "deps": mock_deps,
        }):
            events.append(event)

        assert len(events) > 0
        print(f"  收到 {len(events)} 个事件")

    asyncio.run(_test_query_entry())
    print("  [PASS] query 入口函数")

    print("\n" + "=" * 60)
    print("所有测试完成")
    print("=" * 60)
