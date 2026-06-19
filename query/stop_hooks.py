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


# ---------------------------------------------------------------------------
# 测试块
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import asyncio

    print("=" * 60)
    print("停止钩子测试")
    print("=" * 60)

    # ---- 测试 1: 空消息列表 → 停止 ----
    print("\n--- 测试 1: 空消息列表 → 停止 ---")

    async def _test_empty():
        result = await run_stop_hooks([])
        assert result.should_stop is True
        assert result.reason == "empty_messages"
        print(f"  should_stop={result.should_stop}, reason={result.reason}")

    asyncio.run(_test_empty())
    print("  [PASS] 空消息列表 → 停止")

    # ---- 测试 2: 包含停止指示器 → 停止 ----
    print("\n--- 测试 2: 包含停止指示器 → 停止 ---")

    async def _test_indicator():
        for indicator in ("task complete", "all done", "I'm done now"):
            messages = [
                {"role": "user", "content": "do something"},
                {"role": "assistant", "content": f"Done! {indicator}"},
            ]
            result = await run_stop_hooks(messages)
            assert result.should_stop is True
            assert result.reason is not None
            assert "stop_indicator" in result.reason
            print(f"  '{indicator}' → should_stop=True, reason={result.reason}")

    asyncio.run(_test_indicator())
    print("  [PASS] 包含停止指示器 → 停止")

    # ---- 测试 3: 无停止指示器 → 继续 ----
    print("\n--- 测试 3: 无停止指示器 → 继续 ---")

    async def _test_continue():
        messages = [
            {"role": "user", "content": "do something"},
            {"role": "assistant", "content": "I'll keep working on this."},
        ]
        result = await run_stop_hooks(messages)
        assert result.should_stop is False
        assert result.reason is None
        print(f"  should_stop={result.should_stop}, reason={result.reason}")

    asyncio.run(_test_continue())
    print("  [PASS] 无停止指示器 → 继续")

    # ---- 测试 4: 无 assistant 消息 → 继续 ----
    print("\n--- 测试 4: 无 assistant 消息 → 继续 ---")

    async def _test_no_assistant():
        messages = [
            {"role": "user", "content": "hello"},
        ]
        result = await run_stop_hooks(messages)
        assert result.should_stop is False
        print(f"  should_stop={result.should_stop}")

    asyncio.run(_test_no_assistant())
    print("  [PASS] 无 assistant 消息 → 继续")

    # ---- 测试 5: StopHookResult dataclass ----
    print("\n--- 测试 5: StopHookResult dataclass ---")
    r1 = StopHookResult(should_stop=True, reason="test")
    assert r1.should_stop is True
    assert r1.reason == "test"
    r2 = StopHookResult(should_stop=False)
    assert r2.should_stop is False
    assert r2.reason is None
    print("  [PASS] StopHookResult dataclass")

    # ---- 测试 6: assistant content 非字符串 ----
    print("\n--- 测试 6: assistant content 非字符串 ---")

    async def _test_non_string_content():
        messages = [
            {"role": "user", "content": "do something"},
            {"role": "assistant", "content": None},
        ]
        result = await run_stop_hooks(messages)
        assert result.should_stop is False
        print(f"  should_stop={result.should_stop}")

    asyncio.run(_test_non_string_content())
    print("  [PASS] assistant content 非字符串")

    print("\n" + "=" * 60)
    print("所有测试完成")
    print("=" * 60)
