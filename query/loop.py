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

from dataclasses import asdict, dataclass, field, replace
from typing import TYPE_CHECKING, Any, AsyncGenerator

from query.config import QueryConfig, build_query_config
from query.deps import QueryDeps
from query.stop_hooks import run_stop_hooks
from query.services.api.errors import APIError, classify_error, is_recoverable_error
from query.services.api.llm import StreamEvent, collect_tool_calls
from query.services.compact.auto_compact import CompactTracking
from tools.executor import (
    StreamingToolExecutor,
    ToolExecutionResult,
    tool_result_to_openai_message,
)
from tools import get_tools
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
# _mine_conversation_to_palace - 会话结束自动摄取入 Palace
# ---------------------------------------------------------------------------


def _mine_conversation_to_palace(engine: QueryEngine) -> None:
    """会话结束自动摄取入 Palace。"""
    try:
        from query.services.memory.registry import get_active_memory
        memory = get_active_memory()
        if memory is not None and hasattr(memory, 'mine_conversation'):
            import os
            project_name = os.path.basename(os.getcwd())
            # Convert messages to the format ConversationMiner expects
            convo_messages = []
            for msg in engine.mutable_messages:
                role = msg.get("role", "")
                content = msg.get("content", "")
                if role in ("user", "assistant") and content:
                    if isinstance(content, list):
                        content = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
                    convo_messages.append({"role": role, "content": content})
            if convo_messages:
                memory.mine_conversation(convo_messages, wing=project_name, session_id="loop_session")
    except Exception:
        pass  # 摄取失败不中断


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
    from startup.model.config import get_effective_context_window
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

    # skill 列表增量注入追踪（跨轮保持，避免重复注入）
    sent_skills: set[str] = set()

    # 首轮记忆注入：若有启用的记忆插件，注入 L0+L1 上下文或历史记忆
    if not engine.mutable_messages and user_context is None:
        try:
            from query.services.memory.registry import get_active_memory
            memory = get_active_memory()
            if memory is not None:
                # 优先使用 MemoryPalaceProvider 的 wake_up（L0+L1 上下文）
                if hasattr(memory, 'wake_up'):
                    import os
                    project_name = os.path.basename(os.getcwd())
                    wake_up_text = memory.wake_up(wing=project_name)
                    if wake_up_text and wake_up_text.strip():
                        user_context = {"记忆上下文": wake_up_text}
                else:
                    # 降级：使用通用 search 接口
                    results = await memory.search("", limit=3)
                    if results:
                        mem_text = "\n".join(
                            f"- {r.get('content', '')[:200]}" for r in results if r.get("content")
                        )
                        if mem_text:
                            user_context = {"历史记忆": mem_text}
        except Exception:
            pass  # 记忆检索失败不中断循环

    # eslint-disable-next-line no-constant-condition
    while True:
        # 从引擎读取当前消息
        messages = engine.mutable_messages
        transition = state.transition

        # ---- 0. maxTurns 检查 ----
        if engine_config.max_turns is not None:
            if engine.turn_count >= engine_config.max_turns:
                yield {"type": "max_turns_reached", "max_turns": engine_config.max_turns}
                yield StreamEvent(type="done", finish_reason="stop")
                _mine_conversation_to_palace(engine)
                yield LoopResult(reason="completed")
                return

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
        from prompts import build_system_messages, get_system_prompt_sections

        sections = engine_config.system_prompt_sections or get_system_prompt_sections()
        system_messages = build_system_messages(sections)

        if system_context:
            system_messages = append_system_context(system_messages, system_context)

        # 用户上下文仅临时拼入 api_messages，不污染 messages（messages 会被写回引擎）
        api_messages = messages
        if user_context:
            api_messages = prepend_user_context(api_messages, user_context)

        # skill 列表增量注入（临时，不写回引擎）
        try:
            from tools.skills.bundled import get_model_invocable_skills
            from tools.skills.listing import get_skill_listing_attachment
            from startup.model.config import get_effective_context_window

            invocable_skills = get_model_invocable_skills()
            if invocable_skills:
                context_window = get_effective_context_window(engine_config.model)
                skill_listing = get_skill_listing_attachment(
                    invocable_skills, sent_skills, context_window,
                )
                if skill_listing is not None:
                    api_messages = [skill_listing, *api_messages]
        except ImportError:
            pass

        request = build_api_request(
            messages=api_messages,
            system_prompt=system_messages,
            tools=engine_config.tools,
            model=engine_config.model,
            max_tokens=engine_config.max_tokens,
            temperature=engine_config.temperature,
        )

        # 创建流式工具执行器
        from tools.protocol import ToolUseContext
        tool_executor = StreamingToolExecutor(
            tools=engine_config.tools,
            context=ToolUseContext(),
            permission_check=engine_config.permission_check,
            permission_prompt=engine_config.permission_prompt,
            always_allowed=engine.always_allowed,
        )
        tool_result_messages: list[dict] = []

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

                if event.type == "tool_call_delta":
                    tool_executor.add_delta(event)

                if event.type == "content" and event.content:
                    content_parts.append(event.content)
                elif event.type == "done" and event.finish_reason:
                    finish_reason = event.finish_reason
                elif event.type == "usage" and event.usage:
                    usage_info = event.usage
                elif event.type == "error" and event.error:
                    error_occurred = event.error

                # 流式期间 yield 已完成的工具结果
                for completed in tool_executor.get_completed_results():
                    tr_msg = tool_result_to_openai_message(completed)
                    yield tr_msg
                    tool_result_messages.append(tr_msg)
                    # Skill/Agent 工具可能返回 new_messages（如 skill 正文），注入对话
                    if completed.new_messages:
                        for nm in completed.new_messages:
                            yield nm
                            tool_result_messages.append(nm)
                    # context_modifier 中的 allowed_tools 注入会话级权限
                    if completed.context_modifier and completed.context_modifier.get("allowed_tools"):
                        for t in completed.context_modifier["allowed_tools"]:
                            engine.always_allowed.add(t)

        except Exception as e:
            # 模型调用异常
            yield StreamEvent(
                type="error",
                error=e,
                content=str(e),
            )
            yield LoopResult(reason="model_error", error=e)
            return

        # 流式结束后收尾等待剩余工具
        remaining_results = await tool_executor.get_remaining_results()
        for result in remaining_results:
            tr_msg = tool_result_to_openai_message(result)
            yield tr_msg
            tool_result_messages.append(tr_msg)
            # Skill/Agent 工具可能返回 new_messages，注入对话
            if result.new_messages:
                for nm in result.new_messages:
                    yield nm
                    tool_result_messages.append(nm)
            # context_modifier 中的 allowed_tools 注入会话级权限
            if result.context_modifier and result.context_modifier.get("allowed_tools"):
                for t in result.context_modifier["allowed_tools"]:
                    engine.always_allowed.add(t)

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
                            # 取消流式工具执行器（LLM 没产出有效响应，工具结果不应保留）
                            tool_executor.cancel()
                            updates = {
                                "has_attempted_reactive_compact": True,
                                "transition": "reactive_compact_retry",
                            }
                            state = State(**{**asdict(state), **updates})
                            continue
                    except Exception:
                        pass

                # 压缩恢复失败 → yield 扣留的错误事件 → return
                # 取消流式工具执行器（LLM 没产出有效响应，工具结果不应保留）
                tool_executor.cancel()
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
            _mine_conversation_to_palace(engine)
            yield LoopResult(reason="completed")
            return

        # 8b. 无工具调用 → yield done → return
        if not tool_calls:
            # 运行停止钩子
            stop_result = await run_stop_hooks(messages)
            if stop_result.should_stop:
                yield StreamEvent(type="done", finish_reason="stop")
                _mine_conversation_to_palace(engine)
                yield LoopResult(reason="completed")
                return

            yield StreamEvent(type="done", finish_reason="stop")
            _mine_conversation_to_palace(engine)
            yield LoopResult(reason="completed")
            return

        # ---- 9. 工具结果已在流式期间收集 ----
        # tool_result_messages 已在流式循环和收尾阶段填充

        # ---- 10. 工具结果已在流式期间 yield，此处仅用于状态转换 ----

        # ---- 11. 状态转换 ----
        next_messages = [*messages, *tool_result_messages]
        engine.mutable_messages = next_messages

        # 刷新工具列表（为未来 MCP 接入预留，当前刷新结果和初始一样）
        engine_config = replace(engine_config, tools=get_tools())

        updates = {
            "max_output_tokens_recovery_count": 0,
            "transition": "next_turn",
        }
        state = State(**{**asdict(state), **updates})
