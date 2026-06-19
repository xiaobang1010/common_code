"""压缩模块入口。

按顺序执行四级压缩管线：
1. snip → 如果释放足够 token，返回
2. micro_compact → 如果释放足够 token，返回
3. context_collapse（如果启用）→ 如果释放足够 token，返回
4. auto_compact → 最终压缩

参考原始 TypeScript 实现 src/services/compact/。
"""

from __future__ import annotations

import json
from typing import Any, Callable

from query.services.compact.auto_compact import (
    AUTOCOMPACT_BUFFER_TOKENS,
    CompactTracking,
    auto_compact_if_needed,
    compact_conversation,
    get_auto_compact_threshold,
    should_auto_compact,
)
from query.services.compact.context_collapse import (
    context_collapse_messages,
    should_context_collapse,
)
from query.services.compact.micro_compact import (
    micro_compact_messages,
    should_micro_compact,
)
from query.services.compact.snip import (
    should_snip,
    snip_messages,
)

__all__ = [
    # Snip
    "should_snip",
    "snip_messages",
    # Microcompact
    "should_micro_compact",
    "micro_compact_messages",
    # Context Collapse
    "should_context_collapse",
    "context_collapse_messages",
    # Autocompact
    "CompactTracking",
    "AUTOCOMPACT_BUFFER_TOKENS",
    "should_auto_compact",
    "auto_compact_if_needed",
    "compact_conversation",
    "get_auto_compact_threshold",
    # Pipeline
    "run_compression_pipeline",
]


# ---------------------------------------------------------------------------
# Token 估算
# ---------------------------------------------------------------------------

BYTES_PER_TOKEN = 4


def _estimate_tokens_for_messages(messages: list[dict]) -> int:
    """粗略估算消息列表的 token 数。"""
    total_chars = 0
    for msg in messages:
        total_chars += len(json.dumps(msg, ensure_ascii=False))
    return max(1, total_chars // BYTES_PER_TOKEN)


# ---------------------------------------------------------------------------
# 压缩管线
# ---------------------------------------------------------------------------


async def run_compression_pipeline(
    messages: list[dict],
    model: str,
    tracking: CompactTracking,
    context_collapse_enabled: bool = False,
    microcompact: Callable[..., list[dict]] = micro_compact_messages,
    autocompact: Callable[..., Any] = auto_compact_if_needed,
) -> list[dict]:
    """按顺序执行四级压缩管线。

    执行顺序：
    1. snip → 如果释放足够 token，返回
    2. micro_compact → 如果释放足够 token，返回
    3. context_collapse（如果启用）→ 如果释放足够 token，返回
    4. auto_compact → 最终压缩

    每一级压缩后检查 token 使用量是否降到安全水平
    （context_window 的 85% 以下），如果是则提前返回。

    snip 和 context_collapse 直接使用模块内实现（纯计算 / 不可注入），
    microcompact 和 autocompact 作为参数传入，方便测试注入 mock。

    Args:
        messages: 消息列表
        model: 模型名称
        tracking: 压缩追踪状态
        context_collapse_enabled: 是否启用 context collapse
        microcompact: 微压缩函数（同步，清空旧 tool_result）
        autocompact: 自动压缩函数（异步，全量摘要）

    Returns:
        压缩后的消息列表
    """
    from startup.utils.model.config import get_effective_context_window
    from query.utils.messages import get_messages_after_compact_boundary

    context_window = get_effective_context_window(model)
    # token 估算基于切片后的活跃窗口（最后一个 boundary 之后的消息），
    # 而非完整历史。REPL 传入的 messages 可能含已被压缩的旧消息，
    # 那些不会发给 LLM，不应计入 token 估算。
    active_messages = get_messages_after_compact_boundary(messages)
    current_tokens = _estimate_tokens_for_messages(active_messages)

    # 安全阈值：context_window 的 85%
    safe_threshold = context_window * 0.85

    result = messages

    # ---- 第 1 级: Snip ----
    if should_snip(result, context_window, current_tokens):
        result = snip_messages(result, context_window, current_tokens)
        current_tokens = _estimate_tokens_for_messages(result)
        if current_tokens <= safe_threshold:
            return result

    # ---- 第 2 级: Microcompact ----
    if should_micro_compact(result):
        result = microcompact(messages=result)
        current_tokens = _estimate_tokens_for_messages(result)
        if current_tokens <= safe_threshold:
            return result

    # ---- 第 3 级: Context Collapse（如果启用）----
    if context_collapse_enabled and should_context_collapse(
        result, context_window, current_tokens
    ):
        result = await context_collapse_messages(result, model, context_window)
        current_tokens = _estimate_tokens_for_messages(result)
        if current_tokens <= safe_threshold:
            return result

    # ---- 第 4 级: Autocompact ----
    result, _was_compacted = await autocompact(
        messages=result, model=model, tracking=tracking,
        context_collapse_enabled=context_collapse_enabled,
    )

    return result
