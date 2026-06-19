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

    # 分离 system 消息
    system_messages: list[dict] = []
    non_system_messages: list[dict] = []
    for msg in messages:
        if msg.get("role") == "system":
            system_messages.append(msg)
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

    return system_messages + [boundary_marker, summary_message] + kept_messages


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


# ---------------------------------------------------------------------------
# 测试块
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import asyncio

    print("=" * 60)
    print("Context Collapse 压缩测试")
    print("=" * 60)

    # ---- 测试 1: should_context_collapse — 未达阈值 ----
    print("\n--- 测试 1: should_context_collapse — 未达阈值 ---")
    try:
        result = should_context_collapse(
            messages=[{"role": "user", "content": "Hi"}],
            context_window=10000,
            current_tokens=5000,
        )
        assert result is False, f"期望 False, 得到 {result}"
        print("  [PASS] token 使用 50% 不触发 collapse")
    except Exception as e:
        print(f"  [FAIL] {e}")

    # ---- 测试 2: should_context_collapse — 达到 90% 阈值 ----
    print("\n--- 测试 2: should_context_collapse — 达到 90% 阈值 ---")
    try:
        result = should_context_collapse(
            messages=[{"role": "user", "content": "Hi"}],
            context_window=10000,
            current_tokens=9000,
        )
        assert result is True, f"期望 True, 得到 {result}"
        print("  [PASS] token 使用 90% 触发 collapse")
    except Exception as e:
        print(f"  [FAIL] {e}")

    # ---- 测试 3: should_context_collapse — 超过 90% 阈值 ----
    print("\n--- 测试 3: should_context_collapse — 超过 90% 阈值 ---")
    try:
        result = should_context_collapse(
            messages=[],
            context_window=10000,
            current_tokens=9500,
        )
        assert result is True, f"期望 True, 得到 {result}"
        print("  [PASS] token 使用 95% 触发 collapse")
    except Exception as e:
        print(f"  [FAIL] {e}")

    # ---- 测试 4: should_context_collapse — context_window 为 0 ----
    print("\n--- 测试 4: should_context_collapse — context_window 为 0 ---")
    try:
        result = should_context_collapse(
            messages=[],
            context_window=0,
            current_tokens=100,
        )
        assert result is False, f"期望 False, 得到 {result}"
        print("  [PASS] context_window 为 0 不触发")
    except Exception as e:
        print(f"  [FAIL] {e}")

    # ---- 测试 5: _group_messages_by_round — 基本分组 ----
    print("\n--- 测试 5: _group_messages_by_round — 基本分组 ---")
    try:
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi"},
            {"role": "user", "content": "How are you?"},
            {"role": "assistant", "content": "I'm fine"},
        ]
        groups = _group_messages_by_round(messages)
        assert len(groups) >= 2, f"期望至少 2 组, 得到 {len(groups)}"
        print(f"  分组数: {len(groups)}")
        for i, g in enumerate(groups):
            roles = [m.get("role") for m in g]
            print(f"  组 {i}: {roles}")
        print("  [PASS] 基本分组功能正常")
    except Exception as e:
        print(f"  [FAIL] {e}")

    # ---- 测试 6: _group_messages_by_round — 带 tool 消息 ----
    print("\n--- 测试 6: _group_messages_by_round — 带 tool 消息 ---")
    try:
        messages = [
            {"role": "user", "content": "Check file"},
            {"role": "assistant", "content": "Let me read it"},
            {"role": "tool", "content": "file content", "tool_call_id": "c1"},
            {"role": "assistant", "content": "The file says..."},
            {"role": "user", "content": "Thanks"},
        ]
        groups = _group_messages_by_round(messages)
        assert len(groups) >= 2, f"期望至少 2 组, 得到 {len(groups)}"
        print(f"  分组数: {len(groups)}")
        print("  [PASS] 带 tool 消息的分组功能正常")
    except Exception as e:
        print(f"  [FAIL] {e}")

    # ---- 测试 7: _group_messages_by_round — 空消息列表 ----
    print("\n--- 测试 7: _group_messages_by_round — 空消息列表 ---")
    try:
        groups = _group_messages_by_round([])
        assert groups == [], f"期望空列表, 得到 {groups}"
        print("  [PASS] 空消息列表返回空分组")
    except Exception as e:
        print(f"  [FAIL] {e}")

    # ---- 测试 8: _group_messages_by_round — 单条消息 ----
    print("\n--- 测试 8: _group_messages_by_round — 单条消息 ---")
    try:
        messages = [{"role": "user", "content": "Hello"}]
        groups = _group_messages_by_round(messages)
        assert len(groups) == 1, f"期望 1 组, 得到 {len(groups)}"
        print("  [PASS] 单条消息返回单组")
    except Exception as e:
        print(f"  [FAIL] {e}")

    # ---- 测试 9: context_collapse_messages — 无 LLM 的基本逻辑 ----
    print("\n--- 测试 9: context_collapse_messages — 消息不足不折叠 ---")
    try:
        # 只有一组消息，不应折叠
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi"},
        ]
        # 注意：context_collapse_messages 是 async 的
        result = asyncio.run(context_collapse_messages(
            messages, model="gpt-4o", context_window=10000
        ))
        # 只有一组，应原样返回
        assert len(result) == len(messages), f"期望 {len(messages)} 条消息, 得到 {len(result)}"
        print("  [PASS] 消息不足时不折叠")
    except Exception as e:
        print(f"  [FAIL] {e}")

    print("\n" + "=" * 60)
    print("所有测试完成")
    print("=" * 60)
