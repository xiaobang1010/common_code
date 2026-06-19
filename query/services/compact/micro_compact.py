"""Microcompact 压缩模块（不调 LLM）。

当最后 assistant 消息时间与当前时间差超过阈值时触发，
清空旧 tool_result 内容，保留最近 N 条 tool_result 不压缩。
"""

from __future__ import annotations

import time


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# 时间阈值默认值（分钟）
DEFAULT_GAP_THRESHOLD_MINUTES = 10.0

# 保留最近 N 条 tool_result 不压缩
DEFAULT_KEEP_RECENT_COUNT = 5

# 压缩后的替换文本
COMPACTED_TOOL_RESULT = "[tool result compacted]"


# ---------------------------------------------------------------------------
# Microcompact 接口
# ---------------------------------------------------------------------------


def should_micro_compact(
    messages: list[dict],
    gap_minutes: float = DEFAULT_GAP_THRESHOLD_MINUTES,
) -> bool:
    """判断是否需要 microcompact 压缩。

    当最后 assistant 消息时间与当前时间差超过阈值时触发。

    Args:
        messages: 消息列表
        gap_minutes: 时间阈值（分钟），默认 10 分钟

    Returns:
        是否需要 microcompact
    """
    if not messages:
        return False

    # 查找最后一条 assistant 消息的时间戳
    last_assistant_ts: float | None = None
    for msg in reversed(messages):
        if msg.get("role") == "assistant":
            ts = msg.get("timestamp")
            if ts is not None:
                last_assistant_ts = float(ts)
            break

    if last_assistant_ts is None:
        return False

    # 计算时间差
    gap = (time.time() - last_assistant_ts) / 60.0
    return gap >= gap_minutes


def micro_compact_messages(
    messages: list[dict],
    gap_minutes: float = DEFAULT_GAP_THRESHOLD_MINUTES,
    keep_recent: int = DEFAULT_KEEP_RECENT_COUNT,
) -> list[dict]:
    """清空旧 tool_result 内容。

    遍历消息列表，对于 role=tool 且时间超过阈值的消息，
    将 content 替换为 "[tool result compacted]"。
    保留最近 N 条 tool_result 不压缩。

    Args:
        messages: 消息列表
        gap_minutes: 时间阈值（分钟）
        keep_recent: 保留最近 N 条 tool_result

    Returns:
        压缩后的消息列表
    """
    if not messages:
        return messages

    # 收集所有 tool 消息的索引
    tool_indices: list[int] = []
    for i, msg in enumerate(messages):
        if msg.get("role") == "tool":
            tool_indices.append(i)

    if not tool_indices:
        return messages

    # 保留最近 N 条 tool_result 不压缩
    keep_set = set(tool_indices[-keep_recent:]) if keep_recent > 0 else set()

    # 计算时间阈值
    now = time.time()
    threshold_ts = now - gap_minutes * 60.0

    # 压缩旧 tool_result
    result: list[dict] = []
    for i, msg in enumerate(messages):
        if (
            msg.get("role") == "tool"
            and i not in keep_set
            and _is_old_tool_result(msg, threshold_ts)
        ):
            # 替换 content
            compacted = dict(msg)
            compacted["content"] = COMPACTED_TOOL_RESULT
            result.append(compacted)
        else:
            result.append(msg)

    return result


def _is_old_tool_result(msg: dict, threshold_ts: float) -> bool:
    """判断 tool_result 是否超过时间阈值。

    Args:
        msg: 消息字典
        threshold_ts: 时间阈值（Unix 时间戳）

    Returns:
        是否超过阈值
    """
    ts = msg.get("timestamp")
    if ts is None:
        # 没有时间戳，保守起见不压缩
        return False
    return float(ts) < threshold_ts


# ---------------------------------------------------------------------------
# 测试块
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 60)
    print("Microcompact 压缩测试")
    print("=" * 60)

    # ---- 测试 1: should_micro_compact — 时间差不足 ----
    print("\n--- 测试 1: should_micro_compact — 时间差不足 ---")
    try:
        now = time.time()
        messages = [
            {"role": "assistant", "content": "Hi", "timestamp": now - 60},  # 1 分钟前
        ]
        result = should_micro_compact(messages, gap_minutes=10.0)
        assert result is False, f"期望 False, 得到 {result}"
        print("  [PASS] 时间差 1 分钟 < 10 分钟阈值，不触发")
    except Exception as e:
        print(f"  [FAIL] {e}")

    # ---- 测试 2: should_micro_compact — 时间差超过阈值 ----
    print("\n--- 测试 2: should_micro_compact — 时间差超过阈值 ---")
    try:
        now = time.time()
        messages = [
            {"role": "assistant", "content": "Hi", "timestamp": now - 600},  # 10 分钟前
        ]
        result = should_micro_compact(messages, gap_minutes=10.0)
        assert result is True, f"期望 True, 得到 {result}"
        print("  [PASS] 时间差 10 分钟 >= 10 分钟阈值，触发")
    except Exception as e:
        print(f"  [FAIL] {e}")

    # ---- 测试 3: should_micro_compact — 无 assistant 消息 ----
    print("\n--- 测试 3: should_micro_compact — 无 assistant 消息 ---")
    try:
        messages = [
            {"role": "user", "content": "Hello"},
        ]
        result = should_micro_compact(messages, gap_minutes=10.0)
        assert result is False, f"期望 False, 得到 {result}"
        print("  [PASS] 无 assistant 消息不触发")
    except Exception as e:
        print(f"  [FAIL] {e}")

    # ---- 测试 4: should_micro_compact — 空消息列表 ----
    print("\n--- 测试 4: should_micro_compact — 空消息列表 ---")
    try:
        result = should_micro_compact([], gap_minutes=10.0)
        assert result is False, f"期望 False, 得到 {result}"
        print("  [PASS] 空消息列表不触发")
    except Exception as e:
        print(f"  [FAIL] {e}")

    # ---- 测试 5: should_micro_compact — assistant 无时间戳 ----
    print("\n--- 测试 5: should_micro_compact — assistant 无时间戳 ---")
    try:
        messages = [
            {"role": "assistant", "content": "Hi"},
        ]
        result = should_micro_compact(messages, gap_minutes=10.0)
        assert result is False, f"期望 False, 得到 {result}"
        print("  [PASS] 无时间戳不触发")
    except Exception as e:
        print(f"  [FAIL] {e}")

    # ---- 测试 6: micro_compact_messages — 基本压缩 ----
    print("\n--- 测试 6: micro_compact_messages — 基本压缩 ---")
    try:
        now = time.time()
        old_ts = now - 600  # 10 分钟前
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Let me check", "timestamp": old_ts},
            {"role": "tool", "content": "File content here with lots of data...", "timestamp": old_ts, "tool_call_id": "call_1"},
            {"role": "assistant", "content": "Result", "timestamp": old_ts},
            {"role": "tool", "content": "Another old result", "timestamp": old_ts, "tool_call_id": "call_2"},
            {"role": "tool", "content": "Recent result 1", "timestamp": now, "tool_call_id": "call_3"},
            {"role": "tool", "content": "Recent result 2", "timestamp": now, "tool_call_id": "call_4"},
        ]
        result = micro_compact_messages(messages, gap_minutes=5.0, keep_recent=2)

        # 检查旧的 tool_result 被压缩
        tool_msgs = [m for m in result if m.get("role") == "tool"]
        compacted = [m for m in tool_msgs if m.get("content") == COMPACTED_TOOL_RESULT]
        assert len(compacted) >= 1, f"期望至少 1 条被压缩, 得到 {len(compacted)}"

        # 检查最近的 tool_result 未被压缩
        recent = [m for m in tool_msgs if "Recent result" in m.get("content", "")]
        assert len(recent) == 2, f"期望 2 条保留, 得到 {len(recent)}"

        print(f"  压缩了 {len(compacted)} 条旧 tool_result")
        print("  [PASS] 基本压缩功能正常")
    except Exception as e:
        print(f"  [FAIL] {e}")

    # ---- 测试 7: micro_compact_messages — 无 tool 消息 ----
    print("\n--- 测试 7: micro_compact_messages — 无 tool 消息 ---")
    try:
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi"},
        ]
        result = micro_compact_messages(messages, gap_minutes=5.0)
        assert len(result) == len(messages)
        print("  [PASS] 无 tool 消息时原样返回")
    except Exception as e:
        print(f"  [FAIL] {e}")

    # ---- 测试 8: micro_compact_messages — 空消息列表 ----
    print("\n--- 测试 8: micro_compact_messages — 空消息列表 ---")
    try:
        result = micro_compact_messages([], gap_minutes=5.0)
        assert result == []
        print("  [PASS] 空消息列表原样返回")
    except Exception as e:
        print(f"  [FAIL] {e}")

    # ---- 测试 9: micro_compact_messages — keep_recent=0 ----
    print("\n--- 测试 9: micro_compact_messages — keep_recent=0 ---")
    try:
        now = time.time()
        old_ts = now - 600
        messages = [
            {"role": "tool", "content": "Old result 1", "timestamp": old_ts, "tool_call_id": "c1"},
            {"role": "tool", "content": "Old result 2", "timestamp": old_ts, "tool_call_id": "c2"},
        ]
        result = micro_compact_messages(messages, gap_minutes=5.0, keep_recent=0)
        tool_msgs = [m for m in result if m.get("role") == "tool"]
        compacted = [m for m in tool_msgs if m.get("content") == COMPACTED_TOOL_RESULT]
        assert len(compacted) == 2, f"期望 2 条被压缩, 得到 {len(compacted)}"
        print("  [PASS] keep_recent=0 时所有旧 tool_result 被压缩")
    except Exception as e:
        print(f"  [FAIL] {e}")

    print("\n" + "=" * 60)
    print("所有测试完成")
    print("=" * 60)
