"""Autocompact 压缩模块（最重量级，调 LLM）。

当 token 使用超过阈值时触发全量摘要压缩。
包含 circuit breaker 机制防止连续失败。
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field

# 统一上下文计数收口到 query.utils.tokens
from query.utils.tokens import count_context_tokens


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# Circuit breaker 阈值：连续失败超过此数则停止尝试
MAX_CONSECUTIVE_FAILURES = 3

# 缓冲 token 数：在输出预留之外再留的安全余量
AUTOCOMPACT_BUFFER_TOKENS = 13_000

# 输出预留封顶：阈值计算中 min(max_output, 该值)
MAX_OUTPUT_RESERVE_CAP = 21_000

# 压缩冷却：距上次压缩不足该秒数不再触发
AUTO_COMPACT_COOLDOWN_SECONDS = 30

# "无实质新内容"守卫的计数增幅阈值
NO_NEW_CONTENT_PCT = 0.05

# rapid-refill 熔断：压缩后不足 N 个 assistant 轮组即再触发，连续 M 次停止
RAPID_REFILL_ROUNDS_LIMIT = 3
RAPID_REFILL_CONSECUTIVE_LIMIT = 3


# ---------------------------------------------------------------------------
# 环境变量控制
# ---------------------------------------------------------------------------

# 禁用所有压缩
DISABLE_COMPACT = "DISABLE_COMPACT"
# 仅禁用自动压缩（保留手动 /compact）
DISABLE_AUTO_COMPACT = "DISABLE_AUTO_COMPACT"


def _is_env_truthy(value: str | None) -> bool:
    """检查环境变量是否为真值。"""
    if value is None:
        return False
    return value.lower() in ("1", "true", "yes")


# ---------------------------------------------------------------------------
# CompactTracking dataclass
# ---------------------------------------------------------------------------


@dataclass
class CompactTracking:
    """压缩追踪状态。

    Attributes:
        consecutive_failures: 连续失败次数
        total_failures: 总失败次数
        last_compact_time: 上次压缩时间（Unix 时间戳）
        last_compact_count: 上次压缩后的统一计数快照（冷却/无新内容守卫基线）
        last_compact_assistant_rounds: 上次压缩后活跃窗口内的 assistant 轮组数
    """

    consecutive_failures: int = 0
    total_failures: int = 0
    last_compact_time: float | None = None
    last_compact_count: int | None = None
    last_compact_assistant_rounds: int = 0
    refill_within_rounds: int = 0


# 统一结构化状态枚举值
STATUS_COMPACTED = "compacted"
STATUS_SKIPPED_BELOW = "skipped_below"
STATUS_SKIPPED_NO_NEW = "skipped_no_new"
STATUS_SKIPPED_COOLDOWN = "skipped_cooldown"
STATUS_SKIPPED_RAPID_REFILL = "skipped_rapid_refill"
STATUS_BREAKER = "breaker"
STATUS_FAILED = "failed"


@dataclass
class CompactResult:
    """压缩执行/判定的结构化结果。

    Attributes:
        status: 统一枚举 compacted/skipped_below/skipped_no_new/
            skipped_cooldown/skipped_rapid_refill/breaker/failed
        tokens_before: 判定/压缩前的统一计数
        tokens_after: 压缩后的统一计数（未执行为 0）
        reason: 面向日志与失败文案的补充说明
    """

    status: str
    tokens_before: int = 0
    tokens_after: int = 0
    reason: str = ""


# ---------------------------------------------------------------------------
# 阈值计算
# ---------------------------------------------------------------------------


def get_auto_compact_threshold(model: str) -> int:
    """计算自动压缩阈值。

    阈值 = max(0, 窗口 − min(max_output, 21000) − 13000)；
    窗口与输出上限统一取自模型窗口查询接口（未命中含来源告警）。

    Args:
        model: 模型名称

    Returns:
        自动压缩阈值（token 数，统一计数口径）
    """
    from startup.model.config import get_model_window_info

    context_window, max_output, _source = get_model_window_info(model)
    effective_window = max(0, context_window - min(max_output, MAX_OUTPUT_RESERVE_CAP))
    threshold = max(0, effective_window - AUTOCOMPACT_BUFFER_TOKENS)

    # 环境变量覆盖（用于测试）：作用于含输出预留的有效窗口，取更小值
    env_pct = os.environ.get("CLAUDE_AUTOCOMPACT_PCT_OVERRIDE")
    if env_pct:
        try:
            pct = float(env_pct)
            if 0 < pct <= 100:
                pct_threshold = int(effective_window * (pct / 100))
                return min(pct_threshold, threshold)
        except ValueError:
            pass

    return threshold


# ---------------------------------------------------------------------------
# should_auto_compact
# ---------------------------------------------------------------------------


def should_auto_compact(
    messages: list[dict],
    model: str,
    tracking: CompactTracking,
) -> bool:
    """是否需要执行 autocompact 压缩（布尔判定，兼容既有调用方）。

    判定顺序与守卫细节见 _compact_gate：环境变量禁用 → 熔断 → 冷却 →
    阈值（统一计数口径）→ 无实质新内容守卫。
    """
    return _compact_gate(messages, model, tracking) is None


def _compact_gate(
    messages: list[dict],
    model: str,
    tracking: CompactTracking,
) -> CompactResult | None:
    """压缩判定门：不执行时返回 skipped/breaker 系列状态，放行时返回 None。

    计数与阈值同口径（真实 usage 基线的统一上下文计数）；
    冷却基线与计数快照由会话级 tracking 承载、跨回合生效。
    """
    from query.utils.messages import get_messages_after_compact_boundary

    # 检查环境变量禁用
    if _is_env_truthy(os.environ.get(DISABLE_COMPACT)):
        return CompactResult(STATUS_SKIPPED_BELOW, reason="disabled_by_env")
    if _is_env_truthy(os.environ.get(DISABLE_AUTO_COMPACT)):
        return CompactResult(STATUS_SKIPPED_BELOW, reason="auto_disabled_by_env")

    # Circuit breaker：连续失败超过阈值则停止
    if tracking.consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
        return CompactResult(STATUS_BREAKER, reason="consecutive_failures_exceeded")

    # 冷却：距上次压缩不足 30 秒不再触发
    if (
        tracking.last_compact_time is not None
        and time.time() - tracking.last_compact_time < AUTO_COMPACT_COOLDOWN_SECONDS
    ):
        return CompactResult(STATUS_SKIPPED_COOLDOWN, reason="within_cooldown")

    current_tokens = count_context_tokens(messages)
    threshold = get_auto_compact_threshold(model)
    if current_tokens < threshold:
        return CompactResult(STATUS_SKIPPED_BELOW, tokens_before=current_tokens)

    active = get_messages_after_compact_boundary(messages)
    assistant_rounds_now = sum(1 for m in active if m.get("role") == "assistant")

    # 无实质新内容守卫：上次压缩后无新增 assistant 轮组，或计数增幅 <5%
    if tracking.last_compact_count is not None:
        grew_enough = current_tokens >= tracking.last_compact_count * (1 + NO_NEW_CONTENT_PCT)
        if assistant_rounds_now <= tracking.last_compact_assistant_rounds or not grew_enough:
            return CompactResult(
                STATUS_SKIPPED_NO_NEW,
                tokens_before=current_tokens,
                reason="no_meaningful_new_history_since_last_compact",
            )

    # rapid-refill 熔断：压缩后不足 3 个 assistant 轮组即再次触发、连续 3 次
    # 判定为超大输出在滚雪球，停止自动压缩（计数递增在压缩成功后做，判定门只读）
    if tracking.refill_within_rounds >= RAPID_REFILL_CONSECUTIVE_LIMIT:
        return CompactResult(
            STATUS_SKIPPED_RAPID_REFILL,
            tokens_before=current_tokens,
            reason=(
                "压缩后上下文连续 3 次在 3 个工具轮内再次填满，自动压缩已暂停——"
                "可能存在超大文件或工具输出，请分小块读取，或开始新会话"
            ),
        )

    return None


# ---------------------------------------------------------------------------
# compact_conversation
# ---------------------------------------------------------------------------


async def compact_conversation(
    messages: list[dict],
    model: str,
    keep_groups: int = 1,
    transcript_path: str | None = None,
    custom_instructions: str | None = None,
) -> list[dict]:
    """全量摘要压缩（插入式边界语义）。

    按 assistant 轮组在活跃窗口内计算 pivot：保留最近 keep_groups 组原文
    （自动/应急=1，手动 /compact=0 全量摘要）；在 pivot 处插入
    boundary+摘要，pivot 前的历史原位保留、仅从活跃窗口消失——
    引擎回写与 DB 落库保持全量，界面可完整回放。

    Args:
        messages: 消息列表（全量历史，内部按最后一个压缩边界切活跃窗口）
        model: 模型名称
        keep_groups: 保留最近的 assistant 轮组数，≤0 表示全量摘要

    Returns:
        插入 boundary+摘要后的全量消息列表

    Raises:
        RuntimeError: 活跃窗口内无可压缩消息，或 LLM 调用失败/返回空摘要
    """
    if not messages:
        raise RuntimeError("Not enough messages to compact.")

    from startup.model.config import get_effective_context_window
    from query.utils.messages import (
        compact_pivot_index,
        find_last_compact_boundary_index,
    )

    context_window = get_effective_context_window(model)

    # pivot：首个保留组第一条消息的全局下标；pivot 前=被压缩区
    pivot = compact_pivot_index(messages, keep_groups)
    boundary_idx = find_last_compact_boundary_index(messages)
    active_offset = boundary_idx + 1

    # 本轮待压缩消息：活跃窗口内 pivot 前的非 system 消息
    # （旧摘要 role=user 入列，序列化/折叠层负责以原文内联不再摘要；
    #   skill 正文原位保留，由序列化层排除在压缩输入外）
    messages_to_compact = [
        m for m in messages[active_offset:pivot] if m.get("role") != "system"
    ]

    if not messages_to_compact:
        raise RuntimeError("Not enough messages to compact.")

    # 工程折叠优先：确定性折叠结果足够小（≤ 窗口×15%）直接采纳，跳过 LLM 调用；
    # 超预算则以折叠文本口径构造摘要输入（_generate_compact_summary 内部序列化）
    from query.services.compact.engineering import fold_messages, fold_to_summary_content

    folded_text, folded_est = fold_messages(messages_to_compact)
    # 带自定义指令（/compact [instructions]）时聚焦要求只有 LLM 能理解，
    # 跳过工程折叠直采；无指令且折叠足够小时直接采纳
    if folded_est <= context_window * 0.15 and not (custom_instructions or "").strip():
        summary = fold_to_summary_content(folded_text)
    else:
        summary = await _generate_compact_summary(
            messages_to_compact,
            model,
            pre_folded_text=folded_text,
            custom_instructions=custom_instructions,
        )

    # 创建 compact boundary marker（计数口径与触发判定一致）
    pre_compact_tokens = count_context_tokens(messages)
    boundary_marker = {
        "role": "system",
        "content": (
            f"[Compact Boundary — auto — "
            f"pre-compact tokens: {pre_compact_tokens}]"
        ),
    }

    # 创建摘要消息（标记供前端隐藏与后续压缩排除再摘要）
    from query.services.compact.prompt import get_compact_user_summary_message

    recent_preserved = pivot < len(messages)
    summary_content = get_compact_user_summary_message(
        summary,
        suppress_follow_up_questions=True,
        recent_messages_preserved=recent_preserved,
        transcript_path=transcript_path,
    )

    summary_message = {
        "role": "user",
        "content": summary_content,
        "_compact_summary": True,
    }

    # 若有启用的记忆插件，存储摘要
    try:
        from query.services.memory.registry import get_active_memory
        memory = get_active_memory()
        if memory is not None:
            import asyncio
            import os
            asyncio.ensure_future(memory.store("default", "compact_summary", summary))
            # MemoryPalaceProvider 扩展：同时写入 Drawer 到 Palace
            if hasattr(memory, 'add_drawer'):
                project_name = os.path.basename(os.getcwd())
                memory.add_drawer(
                    wing=project_name,
                    room="session_summary",
                    content=summary,
                    source_file="auto_compact",
                    importance=0.8,
                )
    except Exception:
        pass  # 记忆存储失败不中断压缩

    # 插入式边界：pivot 前的历史原位保留（引擎回写与落库全量），
    # 仅在 pivot 处插入 boundary+摘要；活跃窗口按最后一个边界起切片
    return [*messages[:pivot], boundary_marker, summary_message, *messages[pivot:]]


# 摘要请求 prompt-too-long 的识别模式（覆盖各家供应商的超长错误文案形态）
_PROMPT_TOO_LONG_RE = re.compile(
    r"prompt is too long|maximum context length|context (?:length|window) exceeded|"
    r"too many (?:input )?tokens|input tokens exceed",
    re.IGNORECASE,
)
# "超出量"解析：优先配对数字（如 "145000 tokens > 128000"），
# 再取 "maximum context length is N" 与 "resulted in N" 组合
_TOKEN_GAP_DIRECT_RE = re.compile(r"(\d[\d,]*)\s*tokens?\s*[>>]\s*(\d[\d,]*)", re.IGNORECASE)
_TOKEN_GAP_MAX_RE = re.compile(r"maximum context length is\s*([\d,]+)", re.IGNORECASE)
_TOKEN_GAP_NOW_RE = re.compile(r"(?:resulted in|used)\s*([\d,]+)\s*tokens", re.IGNORECASE)

MAX_COMPACT_INPUT_RETRIES = 3


def _parse_token_gap(message: str) -> int:
    """从错误文案解析需缩减的 token 量，解析不出返回 0（按保底丢组）。"""
    m = _TOKEN_GAP_DIRECT_RE.search(message)
    if m:
        a = int(m.group(1).replace(",", ""))
        b = int(m.group(2).replace(",", ""))
        return max(0, a - b)
    m_max = _TOKEN_GAP_MAX_RE.search(message)
    m_now = _TOKEN_GAP_NOW_RE.search(message)
    if m_max and m_now:
        return max(0, int(m_now.group(1).replace(",", "")) - int(m_max.group(1).replace(",", "")))
    return 0


def _drop_oldest_rounds(messages: list[dict], gap_tokens: int) -> tuple[list[dict], int]:
    """从待摘要消息丢最旧的 assistant 轮组，返回（剩余消息, 丢弃条数）。

    gap 已知时按估算量累计丢弃直到覆盖；解析不出 gap 时保底丢最旧 20% 轮组；
    至少保留 1 组，全丢光则返回空表示无法降级。
    """
    from query.utils.messages import group_rounds

    groups = group_rounds([m for m in messages if m.get("role") != "system"])
    if len(groups) <= 1:
        return [], 0

    drop_upto = len(groups) - 1
    if gap_tokens > 0:
        dropped_tokens = 0
        drop = 0
        for group in groups:
            if drop >= drop_upto:
                break
            dropped_tokens += sum(len(str(m)) for m in group) // 3
            drop += 1
            if dropped_tokens >= gap_tokens:
                break
    else:
        drop = max(1, min(drop_upto, len(groups) // 5))

    remaining = [m for group in groups[drop:] for m in group]
    return remaining, drop


async def _generate_compact_summary(
    messages: list[dict],
    model: str,
    pre_folded_text: str | None = None,
    custom_instructions: str | None = None,
) -> str:
    """调用 LLM 为远期消息生成压缩摘要，带超长降级重试。

    pre_folded_text 提供时以其作为对话输入（工程折叠底稿，工具结果已省略），
    否则按原消息序列化构建。摘要请求本身超出模型窗口时（供应商报
    prompt-too-long 类错误），丢最旧 assistant 轮组重试（≤3 次），
    成功时在摘要头部插入截断标记。

    Args:
        messages: 待压缩的消息列表
        model: 模型名称
        pre_folded_text: 可选的工程折叠文本输入
        custom_instructions: /compact 附加摘要聚焦指令

    Returns:
        摘要文本

    Raises:
        RuntimeError: 重试耗尽仍失败，或返回空摘要
    """
    from query.services.compact.prompt import build_compact_prompt, format_compact_summary, get_compact_prompt
    from query.services.compact.engineering import fold_messages
    from query.services.api.llm import query_model_with_streaming

    # PreCompact hooks：收集压缩指导信息（一次即可，重试复用）
    guidance = ""
    try:
        from server.paths import effective_root
        from startup.hooks import run_pre_compact_hooks
        from startup.setup import get_hooks_snapshot

        hook_snapshot = get_hooks_snapshot()
        if hook_snapshot is not None:
            hook_guidance = await run_pre_compact_hooks(
                hook_snapshot,
                trigger="auto",
                session_id="",
                # cwd 用 effective_root：后台任务上下文里取任务自己的工作区
                cwd=effective_root(),
            )
            if hook_guidance.strip():
                guidance = f"\n\n## Additional compact guidance\n{hook_guidance}"
    except Exception:
        pass  # hook 失败不阻断压缩

    remaining = list(messages)
    truncated_count = 0
    last_error: Exception | None = None

    for attempt in range(MAX_COMPACT_INPUT_RETRIES + 1):
        if attempt == 0 and pre_folded_text is not None:
            compact_prompt = get_compact_prompt(custom_instructions) + (
                f"\n\n<conversation>\n{pre_folded_text}\n</conversation>"
            )
        elif attempt == 0:
            compact_prompt = build_compact_prompt(remaining, custom_instructions)
        else:
            # 重试输入统一走工程折叠（比原始序列化更小，工具结果已省略）
            folded, _est = fold_messages(remaining)
            compact_prompt = get_compact_prompt(custom_instructions) + (
                f"\n\n<conversation>\n{folded}\n</conversation>"
            )
        compact_prompt += guidance

        request_messages = [
            {"role": "system", "content": "You are a helpful AI assistant tasked with summarizing conversations."},
            {"role": "user", "content": compact_prompt},
        ]

        summary_parts: list[str] = []
        try:
            async for event in query_model_with_streaming(
                messages=request_messages,
                model=model,
            ):
                if event.type == "content" and event.content:
                    summary_parts.append(event.content)
                elif event.type == "error":
                    raise RuntimeError(f"LLM 调用失败: {event.content or event.error}")

            summary = "".join(summary_parts)
            if not summary.strip():
                raise RuntimeError("LLM 返回空摘要")

            if truncated_count:
                summary = (
                    "[earlier conversation truncated for compaction retry]\n\n"
                    + summary
                )
            return format_compact_summary(summary)

        except Exception as exc:
            error_text = str(exc)
            last_error = exc
            # 非超长错误或重试耗尽 → 直接抛出
            if not _PROMPT_TOO_LONG_RE.search(error_text) or attempt >= MAX_COMPACT_INPUT_RETRIES:
                raise RuntimeError(error_text) if not isinstance(exc, RuntimeError) else exc
            # 丢最旧轮组缩窄摘要输入后再试
            gap = _parse_token_gap(error_text)
            remaining_next, dropped = _drop_oldest_rounds(remaining, gap)
            if dropped <= 0:
                raise exc
            remaining = remaining_next
            truncated_count += dropped

    # 理论不可达（循环内必 return 或 raise）
    raise last_error if last_error else RuntimeError("摘要生成失败")


# ---------------------------------------------------------------------------
# auto_compact_if_needed
# ---------------------------------------------------------------------------


async def auto_compact_if_needed(
    messages: list[dict],
    model: str,
    tracking: CompactTracking,
    transcript_path: str | None = None,
) -> tuple[list[dict], CompactResult]:
    """自动压缩调度。

    判定门放行则执行 compact_conversation；成功重置连败计数并刷新
    冷却基线与计数快照（供跨回合的冷却/无新内容守卫），失败递增连败。

    Args:
        messages: 消息列表
        model: 模型名称
        tracking: 压缩追踪状态（会话级承载）

    Returns:
        (处理后的消息列表, CompactResult 结构化状态)
    """
    gate = _compact_gate(messages, model, tracking)
    if gate is not None:
        return messages, gate

    tokens_before = count_context_tokens(messages)
    try:
        result_messages = await compact_conversation(
            messages, model, transcript_path=transcript_path
        )
        tokens_after = count_context_tokens(result_messages)

        # 压缩成功：重置连败，刷新冷却基线与快照
        tracking.consecutive_failures = 0
        tracking.total_failures = tracking.total_failures  # 保持不变
        tracking.last_compact_time = time.time()
        # rapid-refill 计数（递增在成功后判定，判定门只读）：
        # 本次触发距上次压缩新增轮组 <3 记为一次快速再满，首次压缩无基线不计
        if tracking.last_compact_count is not None:
            rounds_since = (
                _count_active_assistant_rounds(messages)
                - tracking.last_compact_assistant_rounds
            )
            if rounds_since < RAPID_REFILL_ROUNDS_LIMIT:
                tracking.refill_within_rounds += 1
            else:
                tracking.refill_within_rounds = 0
        tracking.last_compact_count = tokens_after
        tracking.last_compact_assistant_rounds = _count_active_assistant_rounds(
            result_messages
        )

        return result_messages, CompactResult(
            STATUS_COMPACTED, tokens_before=tokens_before, tokens_after=tokens_after
        )

    except Exception as exc:
        # 压缩失败：递增连败（达上限即熔断）
        tracking.consecutive_failures += 1
        tracking.total_failures += 1
        return messages, CompactResult(
            STATUS_FAILED,
            tokens_before=tokens_before,
            reason=str(exc)[:200] or exc.__class__.__name__,
        )


def _count_active_assistant_rounds(messages: list[dict]) -> int:
    """活跃窗口（最后一个压缩边界起）内的 assistant 消息数，轮组数近似。"""
    from query.utils.messages import get_messages_after_compact_boundary

    active = get_messages_after_compact_boundary(messages)
    return sum(1 for m in active if m.get("role") == "assistant")
