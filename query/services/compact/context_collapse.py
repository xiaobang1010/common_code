"""Context Collapse 压缩模块（调 LLM）。

当 token 使用超过 context_window 的 90% 时触发，
按消息组折叠远期消息，调用 LLM 生成折叠摘要。
"""

from __future__ import annotations

import json


# ---------------------------------------------------------------------------
# 粗略 token 估算
# ---------------------------------------------------------------------------

BYTES_PER_TOKEN = 4


def _estimate_tokens(text: str) -> int:
    """粗略估算文本的 token 数。"""
    return max(1, len(text) // BYTES_PER_TOKEN)


def _estimate_tokens_for_messages(messages: list[dict]) -> int:
    """粗略估算消息列表的 token 数。"""
    total_chars = 0
    for msg in messages:
        total_chars += len(json.dumps(msg, ensure_ascii=False))
    return max(1, total_chars // BYTES_PER_TOKEN)


# ---------------------------------------------------------------------------
# 消息分组
# ---------------------------------------------------------------------------


def _group_messages_by_round(messages: list[dict]) -> list[list[dict]]:
    """按消息轮次分组。

    每组包含一个完整的交互轮次：
    - 用户消息 → assistant 回复 → tool_results

    分组规则：
    - 以 assistant 消息作为新轮次的开始
    - tool 消息跟随其前面的 assistant 消息
    - user 消息如果后面紧跟 assistant，归入下一组

    Args:
        messages: 消息列表

    Returns:
        分组后的消息列表
    """
    if not messages:
        return []

    groups: list[list[dict]] = []
    current: list[dict] = []

    for msg in messages:
        role = msg.get("role", "")

        if role == "assistant" and current:
            # 新的 assistant 消息开始新的一组
            groups.append(current)
            current = [msg]
        else:
            current.append(msg)

    if current:
        groups.append(current)

    return groups


# ---------------------------------------------------------------------------
# Context Collapse 接口
# ---------------------------------------------------------------------------


def should_context_collapse(
    messages: list[dict],
    context_window: int,
    current_tokens: int,
) -> bool:
    """判断是否需要 context collapse 压缩。

    当 token 使用超过 context_window 的 90% 时触发。

    Args:
        messages: 消息列表
        context_window: 上下文窗口大小
        current_tokens: 当前 token 使用量

    Returns:
        是否需要 context collapse
    """
    if context_window <= 0:
        return False
    return current_tokens >= context_window * 0.90


async def context_collapse_messages(
    messages: list[dict],
    model: str,
    context_window: int,
) -> list[dict]:
    """按消息组折叠，调用 LLM 生成折叠摘要。

    将消息按"用户消息 → assistant 回复 → tool_results"分组，
    对远期消息组调用 LLM 生成摘要，保留近期消息组不压缩。

    Args:
        messages: 消息列表
        model: 模型名称
        context_window: 上下文窗口大小

    Returns:
        折叠后的消息列表
    """
    if not messages:
        return messages

    # 分离 system 消息和 skill 正文消息（skill 正文不参与折叠）
    system_messages: list[dict] = []
    skill_messages: list[dict] = []
    non_system_messages: list[dict] = []
    for msg in messages:
        if msg.get("role") == "system":
            system_messages.append(msg)
        else:
            from query.utils.messages import is_skill_message
            if is_skill_message(msg):
                skill_messages.append(msg)
            else:
                non_system_messages.append(msg)

    if not non_system_messages:
        return messages

    # 按轮次分组
    groups = _group_messages_by_round(non_system_messages)

    if len(groups) <= 1:
        # 只有一组，无法折叠
        return messages

    # 保留最近 2 组不压缩
    keep_recent_groups = 2
    groups_to_collapse = groups[:-keep_recent_groups]
    groups_to_keep = groups[-keep_recent_groups:]

    if not groups_to_collapse:
        return messages

    # 对远期消息组生成摘要
    try:
        summary = await _generate_collapse_summary(
            groups_to_collapse, model
        )
    except Exception:
        # LLM 调用失败，回退到简单裁剪
        return messages

    # 构建折叠后的消息列表
    boundary_marker = {
        "role": "system",
        "content": f"[Context Collapse Boundary — {len(groups_to_collapse)} rounds collapsed]",
    }

    summary_message = {
        "role": "user",
        "content": f"[Collapsed Context Summary]\n\n{summary}",
    }

    kept_messages = [msg for group in groups_to_keep for msg in group]

    return system_messages + [boundary_marker, summary_message] + skill_messages + kept_messages


async def _generate_collapse_summary(
    groups: list[list[dict]],
    model: str,
) -> str:
    """调用 LLM 为远期消息组生成折叠摘要。

    Args:
        groups: 待折叠的消息组
        model: 模型名称

    Returns:
        摘要文本
    """
    from query.services.compact.prompt import build_compact_prompt
    from query.services.api.llm import query_model_with_streaming

    # 将消息组展平
    flat_messages = [msg for group in groups for msg in group]

    # 构建压缩提示词
    compact_prompt = build_compact_prompt(flat_messages)

    # 构建 LLM 请求消息
    request_messages = [
        {"role": "system", "content": "You are a helpful AI assistant tasked with summarizing conversations."},
        {"role": "user", "content": compact_prompt},
    ]

    # 调用 LLM
    summary_parts: list[str] = []
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

    # 格式化摘要
    from query.services.compact.prompt import format_compact_summary

    return format_compact_summary(summary)
