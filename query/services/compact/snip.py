"""Snip 压缩模块（最轻量，不调 LLM）。

当 token 使用超过 context_window 的 95% 时触发，
从最旧的消息开始删除，直到 token 使用降到 85% 以下。
保留 system 消息和最近的消息。
"""

from __future__ import annotations

# 粗略 token 估算统一收口到 query.utils.tokens，原私有名保留别名
from query.utils.tokens import estimate_tokens_for_messages

_estimate_tokens_for_messages = estimate_tokens_for_messages


# ---------------------------------------------------------------------------
# Snip 压缩接口
# ---------------------------------------------------------------------------


def should_snip(
    messages: list[dict],
    context_window: int,
    current_tokens: int,
) -> bool:
    """判断是否需要 snip 压缩。

    当 token 使用超过 context_window 的 95% 时触发。

    Args:
        messages: 消息列表
        context_window: 上下文窗口大小
        current_tokens: 当前 token 使用量

    Returns:
        是否需要 snip
    """
    if context_window <= 0:
        return False
    return current_tokens >= context_window * 0.95


def snip_messages(
    messages: list[dict],
    context_window: int,
    current_tokens: int,
) -> tuple[list[dict], int]:
    """裁剪历史尾部消息。

    从最旧的消息开始删除，保留 system 消息和最近的消息，
    直到 token 使用降到 context_window 的 85% 以下。

    Args:
        messages: 消息列表
        context_window: 上下文窗口大小
        current_tokens: 当前 token 使用量

    Returns:
        (裁剪后的消息列表, snip 释放的估算 token 数)
    """
    if not messages:
        return [], 0

    target_tokens = context_window * 0.85

    # 分离 system 消息和非 system 消息
    system_messages: list[dict] = []
    non_system_messages: list[dict] = []

    for msg in messages:
        if msg.get("role") == "system":
            system_messages.append(msg)
        else:
            non_system_messages.append(msg)

    # 如果非 system 消息为空，直接返回
    if not non_system_messages:
        return messages, 0

    # 计算当前非 system 消息的 token 数
    non_system_tokens = _estimate_tokens_for_messages(non_system_messages)
    system_tokens = _estimate_tokens_for_messages(system_messages)

    # 如果已经在目标以下，不需要裁剪
    if current_tokens <= target_tokens:
        return messages, 0

    # 从最旧的非 system 消息开始删除
    # 保留最近的消息（从尾部保留）
    kept = list(non_system_messages)
    tokens_freed = 0

    while kept and (current_tokens - tokens_freed) > target_tokens:
        removed = kept.pop(0)
        tokens_freed += _estimate_tokens_for_messages([removed])

    return system_messages + kept, tokens_freed
