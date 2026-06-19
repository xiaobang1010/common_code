"""消息切片辅助函数 — 参考原始 src/utils/messages.ts。

提供 compact boundary 查找和切片功能，让 query loop 和 REPL 共用同一套
"从最后一个压缩边界开始取活跃窗口"的语义。
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# boundary 识别
# ---------------------------------------------------------------------------

# compact_conversation() 生成的 boundary marker content 前缀
# 形如: "[Compact Boundary — auto — pre-compact tokens: 12345]"
_COMPACT_BOUNDARY_PREFIX = "[Compact Boundary"


def is_compact_boundary_message(message: dict) -> bool:
    """判断消息是否为 compact boundary marker。

    boundary marker 的 role 为 system，content 以 "[Compact Boundary" 开头。
    """
    if not isinstance(message, dict):
        return False
    if message.get("role") != "system":
        return False
    content = message.get("content", "")
    if not isinstance(content, str):
        return False
    return content.startswith(_COMPACT_BOUNDARY_PREFIX)


# ---------------------------------------------------------------------------
# 查找与切片
# ---------------------------------------------------------------------------


def find_last_compact_boundary_index(messages: list[dict]) -> int:
    """从后往前查找最后一个 compact boundary marker 的索引。

    Args:
        messages: 消息列表

    Returns:
        最后一个 boundary 的索引；未找到返回 -1
    """
    for i in range(len(messages) - 1, -1, -1):
        if is_compact_boundary_message(messages[i]):
            return i
    return -1


def get_messages_after_compact_boundary(messages: list[dict]) -> list[dict]:
    """从最后一个 compact boundary 开始切片（含 boundary）。

    无 boundary 时返回全部消息。对齐 TS 的 getMessagesAfterCompactBoundary。

    Args:
        messages: 完整消息列表（可能含历史 boundary marker）

    Returns:
        最后一个 boundary 起的切片；无 boundary 则返回原列表
    """
    boundary_idx = find_last_compact_boundary_index(messages)
    if boundary_idx == -1:
        return messages
    return messages[boundary_idx:]


# ---------------------------------------------------------------------------
# 测试块
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 60)
    print("消息切片辅助函数测试")
    print("=" * 60)

    # ---- 测试 1: is_compact_boundary_message ----
    print("\n--- 测试 1: is_compact_boundary_message ---")
    boundary = {"role": "system", "content": "[Compact Boundary — auto — pre-compact tokens: 12345]"}
    assert is_compact_boundary_message(boundary) is True
    assert is_compact_boundary_message({"role": "system", "content": "普通系统消息"}) is False
    assert is_compact_boundary_message({"role": "user", "content": "[Compact Boundary"}) is False
    assert is_compact_boundary_message({}) is False
    assert is_compact_boundary_message({"role": "system", "content": 123}) is False
    print("  [PASS] is_compact_boundary_message")

    # ---- 测试 2: find_last_compact_boundary_index — 无 boundary ----
    print("\n--- 测试 2: find_last_compact_boundary_index — 无 boundary ---")
    msgs = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]
    assert find_last_compact_boundary_index(msgs) == -1
    print("  [PASS] 无 boundary 返回 -1")

    # ---- 测试 3: find_last_compact_boundary_index — 有 boundary ----
    print("\n--- 测试 3: find_last_compact_boundary_index — 有 boundary ---")
    msgs = [
        {"role": "user", "content": "old msg"},
        {"role": "system", "content": "[Compact Boundary — auto — pre-compact tokens: 500]"},
        {"role": "user", "content": "summary here"},
        {"role": "assistant", "content": "recent reply"},
    ]
    idx = find_last_compact_boundary_index(msgs)
    assert idx == 1, f"期望 1, 得到 {idx}"
    print(f"  boundary 索引: {idx}")
    print("  [PASS] 找到 boundary 索引")

    # ---- 测试 4: find_last_compact_boundary_index — 多个 boundary 取最后 ----
    print("\n--- 测试 4: find_last_compact_boundary_index — 多个 boundary ---")
    msgs = [
        {"role": "system", "content": "[Compact Boundary — auto — pre-compact tokens: 100]"},
        {"role": "user", "content": "first summary"},
        {"role": "system", "content": "[Compact Boundary — auto — pre-compact tokens: 200]"},
        {"role": "user", "content": "second summary"},
    ]
    idx = find_last_compact_boundary_index(msgs)
    assert idx == 2, f"期望 2, 得到 {idx}"
    print(f"  最后一个 boundary 索引: {idx}")
    print("  [PASS] 多个 boundary 取最后")

    # ---- 测试 5: get_messages_after_compact_boundary — 无 boundary ----
    print("\n--- 测试 5: get_messages_after_compact_boundary — 无 boundary ---")
    msgs = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]
    result = get_messages_after_compact_boundary(msgs)
    assert result is msgs, "无 boundary 应返回原列表"
    assert len(result) == 2
    print("  [PASS] 无 boundary 返回全部")

    # ---- 测试 6: get_messages_after_compact_boundary — 有 boundary ----
    print("\n--- 测试 6: get_messages_after_compact_boundary — 有 boundary ---")
    msgs = [
        {"role": "user", "content": "old msg"},
        {"role": "system", "content": "[Compact Boundary — auto — pre-compact tokens: 500]"},
        {"role": "user", "content": "summary"},
        {"role": "assistant", "content": "recent"},
    ]
    result = get_messages_after_compact_boundary(msgs)
    assert len(result) == 3, f"期望 3 条, 得到 {len(result)}"
    assert result[0]["content"].startswith("[Compact Boundary")
    assert result[1]["content"] == "summary"
    assert result[2]["content"] == "recent"
    print(f"  切片长度: {len(result)}")
    print("  [PASS] 从 boundary 开始切片")

    # ---- 测试 7: 空列表 ----
    print("\n--- 测试 7: 空列表 ---")
    assert find_last_compact_boundary_index([]) == -1
    assert get_messages_after_compact_boundary([]) == []
    print("  [PASS] 空列表处理")

    print("\n" + "=" * 60)
    print("所有测试完成")
    print("=" * 60)
