"""Microcompact 压缩模块（不调 LLM）。

触发条件：距最后一条 assistant 消息闲置 ≥60 分钟，
或统一计数达到压缩阈值前方的水位线。
清空白名单工具的旧 tool_result（占位符携带来源工具名），
按 assistant 轮组保留最近 5 组原文；节省不足门槛则跳过。
"""

from __future__ import annotations

import time

from query.services.compact.prompt import build_tool_name_map, _extract_message_text
from query.utils.messages import (
    get_messages_after_compact_boundary,
    group_rounds,
)
from query.utils.tokens import CONTEXT_CHARS_PER_TOKEN

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# 闲置触发阈值（分钟）
DEFAULT_IDLE_THRESHOLD_MINUTES = 60.0

# 保留最近 N 个 assistant 轮组的工具结果原文
DEFAULT_KEEP_RECENT_ROUNDS = 5

# 单次清省低于该估算 token 数则跳过（避免无收益改写）
MIN_TOKEN_SAVINGS = 256

# 水位触发：min(0.9×自动压缩阈值, 阈值−2000)
WATERLINE_RATIO = 0.9
WATERLINE_MARGIN = 2000

# 候选工具白名单（WebSearch 项目暂无、预留不列）
MICRO_CANDIDATE_TOOLS = frozenset({
    "Read", "Bash", "Grep", "Glob", "WebFetch", "Edit", "Write",
})


def _placeholder_text(tool_name: str) -> str:
    """占位文本：携带工具名，模型知道省略了哪类输出。"""
    return f"[Old tool result content cleared — {tool_name}]"


def _message_ts(msg: dict) -> float | None:
    """读取消息时间戳（毫秒口径 _ts），无则 None。"""
    ts = msg.get("_ts")
    if ts is None:
        ts = msg.get("timestamp")
    if ts is None:
        return None
    try:
        return float(ts)
    except (TypeError, ValueError):
        return None


def should_micro_compact(
    messages: list[dict],
    model: str = "",
    gap_minutes: float = DEFAULT_IDLE_THRESHOLD_MINUTES,
) -> bool:
    """判断是否需要微压缩：闲置超时 或 token 压力水位。

    Args:
        messages: 消息列表（全量或活跃窗口均可，按最后一个边界切活跃窗口判断）
        model: 模型名称（水位阈值计算用；空串跳过水位判断只判闲置）
        gap_minutes: 闲置阈值（分钟）

    Returns:
        是否需要微压缩
    """
    if not messages:
        return False

    active = get_messages_after_compact_boundary(messages)

    # 时间触发：距最后一条 assistant 消息的间隔
    last_assistant_ts: float | None = None
    for msg in reversed(active):
        if msg.get("role") == "assistant":
            last_assistant_ts = _message_ts(msg)
            break
    if last_assistant_ts is not None:
        # _ts 为毫秒口径
        gap = (time.time() * 1000 - last_assistant_ts) / 60000.0
        if gap >= gap_minutes:
            return True

    # 水位触发：统一计数逼近自动压缩阈值
    if model:
        from query.services.compact.auto_compact import get_auto_compact_threshold
        from query.utils.tokens import count_context_tokens

        threshold = get_auto_compact_threshold(model)
        # 极低测试阈值下 margin 可能把水位压成非正数，钳到至少 1 防其永真
        waterline = max(1, min(int(threshold * WATERLINE_RATIO), threshold - WATERLINE_MARGIN))
        if count_context_tokens(messages) >= waterline:
            return True

    return False


def micro_compact_messages(
    messages: list[dict],
    model: str = "",
    keep_recent_rounds: int = DEFAULT_KEEP_RECENT_ROUNDS,
    min_savings: int = MIN_TOKEN_SAVINGS,
) -> list[dict]:
    """清空白名单工具的旧 tool_result（最近 N 个轮组保留原文）。

    Args:
        messages: 消息列表
        model: 模型名称（保留形参供调用口径统一，本函数不使用）
        keep_recent_rounds: 保留最近多少个 assistant 轮组的工具结果
        min_savings: 最小估算节省 token 数，低于则不改写

    Returns:
        处理后的消息列表（未达门槛或无候选时返回原列表）
    """
    if not messages:
        return messages

    # 只处理活跃窗口：边界前的历史本就不发给模型，改写无收益
    boundary_idx = len(messages) - len(get_messages_after_compact_boundary(messages))
    active = messages[boundary_idx:]
    if not active:
        return messages

    name_map = build_tool_name_map(active)
    groups = group_rounds([m for m in active if m.get("role") != "system"])
    # 标记保留组：最近 N 个轮组内的消息 id 集合
    kept_ids: set[int] = set()
    for group in groups[-keep_recent_rounds:] if keep_recent_rounds > 0 else []:
        for msg in group:
            kept_ids.add(id(msg))

    cleared: list[tuple[int, int]] = []  # (活跃窗口下标, 释放字符数)
    for i, msg in enumerate(active):
        if msg.get("role") != "tool" or id(msg) in kept_ids:
            continue
        tool_name = name_map.get(msg.get("tool_call_id", ""), "")
        if tool_name not in MICRO_CANDIDATE_TOOLS:
            continue
        text = _extract_message_text(msg)
        # 合成占位与中断占位不重复计入收益
        if text and not text.startswith("[Old tool result content cleared") and \
                not text.startswith("[执行被中断"):
            cleared.append((i, len(text)))

    saved_tokens = sum(n // CONTEXT_CHARS_PER_TOKEN for _, n in cleared)
    if saved_tokens < min_savings:
        return messages

    new_active = list(active)
    for i, _n in cleared:
        replacement = dict(new_active[i])
        name_map_i = name_map.get(replacement.get("tool_call_id", ""), "tool")
        replacement["content"] = _placeholder_text(name_map_i)
        new_active[i] = replacement
    return [*messages[:boundary_idx], *new_active]
