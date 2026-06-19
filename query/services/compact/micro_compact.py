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
