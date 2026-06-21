"""停止钩子 — 检查是否满足停止条件。

在模型响应完成后执行，决定是否应该终止循环。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


# ---------------------------------------------------------------------------
# StopHookResult — 停止钩子结果
# ---------------------------------------------------------------------------


@dataclass
class StopHookResult:
    """停止钩子执行结果。

    Attributes:
        should_stop: 是否应该停止循环
        reason: 停止原因（should_stop=True 时有值）
    """

    should_stop: bool
    reason: str | None = None


# ---------------------------------------------------------------------------
# _STOP_INDICATORS — 停止指示器
# ---------------------------------------------------------------------------

# 当 assistant 消息包含这些内容时，认为应该停止
_STOP_INDICATORS = (
    "task complete",
    "task completed",
    "all done",
    "i'm done",
    "i am done",
    "no further action",
    "nothing more to do",
)


# ---------------------------------------------------------------------------
# run_stop_hooks — 执行停止钩子
# ---------------------------------------------------------------------------


async def run_stop_hooks(messages: list[dict]) -> StopHookResult:
    """执行停止钩子，检查是否满足停止条件。

    检查逻辑：
    1. 如果最后一条 assistant 消息包含停止指示器 → should_stop=True
    2. 如果消息列表为空 → should_stop=True
    3. 否则 → should_stop=False

    Args:
        messages: 消息列表

    Returns:
        StopHookResult
    """
    if not messages:
        return StopHookResult(should_stop=True, reason="empty_messages")

    # 查找最后一条 assistant 消息
    last_assistant_msg = None
    for msg in reversed(messages):
        if msg.get("role") == "assistant":
            last_assistant_msg = msg
            break

    if last_assistant_msg is None:
        return StopHookResult(should_stop=False, reason=None)

    # 检查内容是否包含停止指示器
    content = last_assistant_msg.get("content", "")
    if isinstance(content, str):
        content_lower = content.lower()
        for indicator in _STOP_INDICATORS:
            if indicator in content_lower:
                return StopHookResult(
                    should_stop=True,
                    reason=f"stop_indicator:{indicator}",
                )

    return StopHookResult(should_stop=False, reason=None)
