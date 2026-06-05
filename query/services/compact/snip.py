"""Snip 压缩模块（最轻量，不调 LLM）。

当 token 使用超过 context_window 的 95% 时触发，
从最旧的消息开始删除，直到 token 使用降到 85% 以下。
保留 system 消息和最近的消息。
"""

from __future__ import annotations

import json


# ---------------------------------------------------------------------------
# 粗略 token 估算
# ---------------------------------------------------------------------------

# 默认 bytes-per-token 比率（约 4 字符 = 1 token）
BYTES_PER_TOKEN = 4


def _estimate_tokens_for_messages(messages: list[dict]) -> int:
    """粗略估算消息列表的 token 数。

    Args:
        messages: 消息列表（dict 格式）

    Returns:
        估算的 token 数
    """
    total_chars = 0
    for msg in messages:
        # 序列化整个消息为 JSON 字符串来估算
        total_chars += len(json.dumps(msg, ensure_ascii=False))
    return max(1, total_chars // BYTES_PER_TOKEN)


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
) -> list[dict]:
    """裁剪历史尾部消息。

    从最旧的消息开始删除，保留 system 消息和最近的消息，
    直到 token 使用降到 context_window 的 85% 以下。

    Args:
        messages: 消息列表
        context_window: 上下文窗口大小
        current_tokens: 当前 token 使用量

    Returns:
        裁剪后的消息列表
    """
    if not messages:
        return messages

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
        return messages

    # 计算当前非 system 消息的 token 数
    non_system_tokens = _estimate_tokens_for_messages(non_system_messages)
    system_tokens = _estimate_tokens_for_messages(system_messages)

    # 如果已经在目标以下，不需要裁剪
    if current_tokens <= target_tokens:
        return messages

    # 从最旧的非 system 消息开始删除
    # 保留最近的消息（从尾部保留）
    kept = list(non_system_messages)
    tokens_freed = 0

    while kept and (current_tokens - tokens_freed) > target_tokens:
        removed = kept.pop(0)
        tokens_freed += _estimate_tokens_for_messages([removed])

    return system_messages + kept


# ---------------------------------------------------------------------------
# 测试块
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 60)
    print("Snip 压缩测试")
    print("=" * 60)

    # ---- 测试 1: should_snip — 未达阈值 ----
    print("\n--- 测试 1: should_snip — 未达阈值 ---")
    try:
        messages = [{"role": "user", "content": "Hello"}]
        result = should_snip(messages, context_window=10000, current_tokens=5000)
        assert result is False, f"期望 False, 得到 {result}"
        print("  [PASS] token 使用 50% 不触发 snip")
    except Exception as e:
        print(f"  [FAIL] {e}")

    # ---- 测试 2: should_snip — 达到 95% 阈值 ----
    print("\n--- 测试 2: should_snip — 达到 95% 阈值 ---")
    try:
        messages = [{"role": "user", "content": "Hello"}]
        result = should_snip(messages, context_window=10000, current_tokens=9500)
        assert result is True, f"期望 True, 得到 {result}"
        print("  [PASS] token 使用 95% 触发 snip")
    except Exception as e:
        print(f"  [FAIL] {e}")

    # ---- 测试 3: should_snip — 超过 95% 阈值 ----
    print("\n--- 测试 3: should_snip — 超过 95% 阈值 ---")
    try:
        result = should_snip([], context_window=10000, current_tokens=9800)
        assert result is True, f"期望 True, 得到 {result}"
        print("  [PASS] token 使用 98% 触发 snip")
    except Exception as e:
        print(f"  [FAIL] {e}")

    # ---- 测试 4: should_snip — context_window 为 0 ----
    print("\n--- 测试 4: should_snip — context_window 为 0 ---")
    try:
        result = should_snip([], context_window=0, current_tokens=100)
        assert result is False, f"期望 False, 得到 {result}"
        print("  [PASS] context_window 为 0 不触发")
    except Exception as e:
        print(f"  [FAIL] {e}")

    # ---- 测试 5: snip_messages — 基本裁剪 ----
    print("\n--- 测试 5: snip_messages — 基本裁剪 ---")
    try:
        # 创建大量消息使 token 超过 95%
        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
        ]
        # 添加 100 条消息，每条约 50 字符 ≈ 12 tokens
        for i in range(100):
            messages.append({"role": "user", "content": f"This is message number {i} with some padding text to add tokens."})
            messages.append({"role": "assistant", "content": f"Response to message {i} with some padding text."})

        context_window = 1000  # 小窗口，容易触发
        current_tokens = _estimate_tokens_for_messages(messages)

        result = snip_messages(messages, context_window, current_tokens)

        # 验证 system 消息被保留
        system_msgs = [m for m in result if m.get("role") == "system"]
        assert len(system_msgs) == 1, f"期望 1 条 system 消息, 得到 {len(system_msgs)}"

        # 验证结果 token 数在目标以下
        result_tokens = _estimate_tokens_for_messages(result)
        target = context_window * 0.85
        assert result_tokens <= target + 50, f"结果 token {result_tokens} 超过目标 {target}"

        # 验证保留了最近的消息
        if len(result) > 1:
            last_msg = result[-1]
            assert "99" in last_msg.get("content", ""), "应保留最近的消息"

        print(f"  原始消息数: {len(messages)}, 裁剪后: {len(result)}")
        print(f"  原始 token: {current_tokens}, 裁剪后: {result_tokens}")
        print("  [PASS] 基本裁剪功能正常")
    except Exception as e:
        print(f"  [FAIL] {e}")

    # ---- 测试 6: snip_messages — 不需要裁剪 ----
    print("\n--- 测试 6: snip_messages — 不需要裁剪 ---")
    try:
        messages = [
            {"role": "system", "content": "System"},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi"},
        ]
        context_window = 10000
        current_tokens = _estimate_tokens_for_messages(messages)

        result = snip_messages(messages, context_window, current_tokens)
        assert len(result) == len(messages), f"期望 {len(messages)} 条消息, 得到 {len(result)}"
        print("  [PASS] token 使用低时不裁剪")
    except Exception as e:
        print(f"  [FAIL] {e}")

    # ---- 测试 7: snip_messages — 空消息列表 ----
    print("\n--- 测试 7: snip_messages — 空消息列表 ---")
    try:
        result = snip_messages([], context_window=10000, current_tokens=0)
        assert result == [], f"期望空列表, 得到 {result}"
        print("  [PASS] 空消息列表原样返回")
    except Exception as e:
        print(f"  [FAIL] {e}")

    # ---- 测试 8: snip_messages — 只有 system 消息 ----
    print("\n--- 测试 8: snip_messages — 只有 system 消息 ---")
    try:
        messages = [
            {"role": "system", "content": "You are helpful."},
        ]
        result = snip_messages(messages, context_window=10, current_tokens=100)
        assert len(result) == 1, f"期望 1 条消息, 得到 {len(result)}"
        assert result[0]["role"] == "system"
        print("  [PASS] 只有 system 消息时保留")
    except Exception as e:
        print(f"  [FAIL] {e}")

    print("\n" + "=" * 60)
    print("所有测试完成")
    print("=" * 60)
