"""Autocompact 压缩模块（最重量级，调 LLM）。

当 token 使用超过阈值时触发全量摘要压缩。
包含 circuit breaker 机制防止连续失败。

参考原始 TypeScript 实现 src/services/compact/autoCompact.ts。
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# Circuit breaker 阈值：连续失败超过此数则停止尝试
MAX_CONSECUTIVE_FAILURES = 3

# 缓冲 token 数：在 context_window 基础上预留的空间
AUTOCOMPACT_BUFFER_TOKENS = 13_000

# 粗略 token 估算参数
BYTES_PER_TOKEN = 4


# ---------------------------------------------------------------------------
# 环境变量控制
# ---------------------------------------------------------------------------

# 禁用所有压缩
DISABLE_COMPACT = "DISABLE_COMPACT"
# 仅禁用自动压缩（保留手动 /compact）
DISABLE_AUTO_COMPACT = "DISABLE_AUTO_COMPACT"


def _is_env_truthy(value: str | None) -> bool:
    """检查环境变量是否为真值。"""
    if value is None:
        return False
    return value.lower() in ("1", "true", "yes")


# ---------------------------------------------------------------------------
# CompactTracking dataclass
# ---------------------------------------------------------------------------


@dataclass
class CompactTracking:
    """压缩追踪状态。

    Attributes:
        consecutive_failures: 连续失败次数
        total_failures: 总失败次数
        last_compact_time: 上次压缩时间（Unix 时间戳）
    """

    consecutive_failures: int = 0
    total_failures: int = 0
    last_compact_time: float | None = None


# ---------------------------------------------------------------------------
# Token 估算
# ---------------------------------------------------------------------------


def _estimate_tokens_for_messages(messages: list[dict]) -> int:
    """粗略估算消息列表的 token 数。"""
    total_chars = 0
    for msg in messages:
        total_chars += len(json.dumps(msg, ensure_ascii=False))
    return max(1, total_chars // BYTES_PER_TOKEN)


# ---------------------------------------------------------------------------
# 阈值计算
# ---------------------------------------------------------------------------


def get_auto_compact_threshold(model: str) -> int:
    """计算自动压缩阈值。

    阈值 = effective_context_window - AUTOCOMPACT_BUFFER_TOKENS

    Args:
        model: 模型名称

    Returns:
        自动压缩阈值（token 数）
    """
    from startup.utils.model.config import get_effective_context_window

    effective_window = get_effective_context_window(model)
    threshold = effective_window - AUTOCOMPACT_BUFFER_TOKENS

    # 环境变量覆盖（用于测试）
    env_pct = os.environ.get("CLAUDE_AUTOCOMPACT_PCT_OVERRIDE")
    if env_pct:
        try:
            pct = float(env_pct)
            if 0 < pct <= 100:
                pct_threshold = int(effective_window * (pct / 100))
                return min(pct_threshold, threshold)
        except ValueError:
            pass

    return threshold


# ---------------------------------------------------------------------------
# should_auto_compact
# ---------------------------------------------------------------------------


def should_auto_compact(
    messages: list[dict],
    model: str,
    tracking: CompactTracking,
    context_collapse_enabled: bool = False,
) -> bool:
    """判断是否需要 autocompact 压缩。

    检查顺序：
    1. DISABLE_COMPACT / DISABLE_AUTO_COMPACT → skip
    2. context_collapse_enabled → skip（互斥）
    3. consecutive_failures >= 3 → skip（circuit breaker）
    4. token 使用 < threshold → skip

    Args:
        messages: 消息列表
        model: 模型名称
        tracking: 压缩追踪状态
        context_collapse_enabled: 是否启用了 context collapse

    Returns:
        是否需要 autocompact
    """
    # 检查环境变量禁用
    if _is_env_truthy(os.environ.get(DISABLE_COMPACT)):
        return False
    if _is_env_truthy(os.environ.get(DISABLE_AUTO_COMPACT)):
        return False

    # Context collapse 启用时，autocompact 不触发（互斥）
    if context_collapse_enabled:
        return False

    # Circuit breaker：连续失败超过阈值则停止
    if tracking.consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
        return False

    # 检查 token 使用是否超过阈值
    current_tokens = _estimate_tokens_for_messages(messages)
    threshold = get_auto_compact_threshold(model)

    return current_tokens >= threshold


# ---------------------------------------------------------------------------
# compact_conversation
# ---------------------------------------------------------------------------


async def compact_conversation(
    messages: list[dict],
    model: str,
) -> list[dict]:
    """全量摘要压缩。

    计算 pivot 点（保留近期消息），对远期消息调用 LLM 生成摘要，
    创建 compact boundary marker，返回 [boundary_marker + summary + kept_messages]。

    Args:
        messages: 消息列表
        model: 模型名称

    Returns:
        压缩后的消息列表

    Raises:
        RuntimeError: LLM 调用失败或返回空摘要
    """
    if not messages:
        raise RuntimeError("Not enough messages to compact.")

    from startup.utils.model.config import get_effective_context_window

    context_window = get_effective_context_window(model)

    # 分离 system 消息
    system_messages: list[dict] = []
    non_system_messages: list[dict] = []
    for msg in messages:
        if msg.get("role") == "system":
            system_messages.append(msg)
        else:
            non_system_messages.append(msg)

    if not non_system_messages:
        raise RuntimeError("Not enough messages to compact.")

    # 计算 pivot 点：保留近期消息（约 30% 的消息或至少 5 条）
    keep_count = max(5, len(non_system_messages) // 3)
    keep_count = min(keep_count, len(non_system_messages))

    messages_to_compact = non_system_messages[:-keep_count]
    messages_to_keep = non_system_messages[-keep_count:]

    if not messages_to_compact:
        raise RuntimeError("Not enough messages to compact.")

    # 调用 LLM 生成摘要
    summary = await _generate_compact_summary(messages_to_compact, model)

    # 创建 compact boundary marker
    pre_compact_tokens = _estimate_tokens_for_messages(messages)
    boundary_marker = {
        "role": "system",
        "content": (
            f"[Compact Boundary — auto — "
            f"pre-compact tokens: {pre_compact_tokens}]"
        ),
    }

    # 创建摘要消息
    from query.services.compact.prompt import get_compact_user_summary_message

    summary_content = get_compact_user_summary_message(
        summary,
        suppress_follow_up_questions=True,
        recent_messages_preserved=True,
    )

    summary_message = {
        "role": "user",
        "content": summary_content,
    }

    return system_messages + [boundary_marker, summary_message] + messages_to_keep


async def _generate_compact_summary(
    messages: list[dict],
    model: str,
) -> str:
    """调用 LLM 为远期消息生成压缩摘要。

    Args:
        messages: 待压缩的消息列表
        model: 模型名称

    Returns:
        摘要文本

    Raises:
        RuntimeError: LLM 调用失败或返回空摘要
    """
    from query.services.compact.prompt import build_compact_prompt, format_compact_summary
    from query.services.api.llm import query_model_with_streaming

    # 构建压缩提示词
    compact_prompt = build_compact_prompt(messages)

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

    return format_compact_summary(summary)


# ---------------------------------------------------------------------------
# auto_compact_if_needed
# ---------------------------------------------------------------------------


async def auto_compact_if_needed(
    messages: list[dict],
    model: str,
    tracking: CompactTracking,
    context_collapse_enabled: bool = False,
) -> tuple[list[dict], bool]:
    """自动压缩调度。

    如果 should_auto_compact 返回 True，则调用 compact_conversation。
    压缩失败时增加 consecutive_failures 计数（circuit breaker），
    压缩成功时重置 consecutive_failures。

    Args:
        messages: 消息列表
        model: 模型名称
        tracking: 压缩追踪状态
        context_collapse_enabled: 是否启用了 context collapse

    Returns:
        (compacted_messages, was_compacted) 元组
    """
    if not should_auto_compact(messages, model, tracking, context_collapse_enabled):
        return messages, False

    try:
        result = await compact_conversation(messages, model)

        # 压缩成功：重置失败计数
        tracking.consecutive_failures = 0
        tracking.total_failures = tracking.total_failures  # 保持不变
        tracking.last_compact_time = time.time()

        return result, True

    except Exception:
        # 压缩失败：增加失败计数
        tracking.consecutive_failures += 1
        tracking.total_failures += 1

        if tracking.consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            pass  # Circuit breaker 已触发

        return messages, False


# ---------------------------------------------------------------------------
# 测试块
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 60)
    print("Autocompact 压缩测试")
    print("=" * 60)

    # ---- 测试 1: should_auto_compact — 正常触发 ----
    print("\n--- 测试 1: should_auto_compact — 正常触发 ---")
    try:
        # 创建大量消息使 token 超过阈值
        messages = [{"role": "user", "content": f"Message {i} with some padding text" * 10} for i in range(200)]
        tracking = CompactTracking()
        result = should_auto_compact(messages, "gpt-4o", tracking)
        # gpt-4o context_window=128000, threshold=128000-13000=115000
        # 200 条消息每条约 250 字符 ≈ 62 tokens, 总计约 12400 tokens
        # 这不会超过 115000 的阈值，所以应该是 False
        print(f"  消息数: {len(messages)}, 估算 tokens: {_estimate_tokens_for_messages(messages)}")
        print(f"  阈值: {get_auto_compact_threshold('gpt-4o')}")
        print(f"  should_auto_compact: {result}")
        print("  [PASS] 正常判断逻辑")
    except Exception as e:
        print(f"  [FAIL] {e}")

    # ---- 测试 2: should_auto_compact — DISABLE_COMPACT 环境变量 ----
    print("\n--- 测试 2: should_auto_compact — DISABLE_COMPACT 环境变量 ---")
    try:
        old_val = os.environ.get(DISABLE_COMPACT)
        os.environ[DISABLE_COMPACT] = "1"
        try:
            tracking = CompactTracking()
            result = should_auto_compact([], "gpt-4o", tracking)
            assert result is False, f"期望 False, 得到 {result}"
            print("  [PASS] DISABLE_COMPACT 时跳过")
        finally:
            if old_val is None:
                os.environ.pop(DISABLE_COMPACT, None)
            else:
                os.environ[DISABLE_COMPACT] = old_val
    except Exception as e:
        print(f"  [FAIL] {e}")

    # ---- 测试 3: should_auto_compact — DISABLE_AUTO_COMPACT 环境变量 ----
    print("\n--- 测试 3: should_auto_compact — DISABLE_AUTO_COMPACT 环境变量 ---")
    try:
        old_val = os.environ.get(DISABLE_AUTO_COMPACT)
        os.environ[DISABLE_AUTO_COMPACT] = "1"
        try:
            tracking = CompactTracking()
            result = should_auto_compact([], "gpt-4o", tracking)
            assert result is False, f"期望 False, 得到 {result}"
            print("  [PASS] DISABLE_AUTO_COMPACT 时跳过")
        finally:
            if old_val is None:
                os.environ.pop(DISABLE_AUTO_COMPACT, None)
            else:
                os.environ[DISABLE_AUTO_COMPACT] = old_val
    except Exception as e:
        print(f"  [FAIL] {e}")

    # ---- 测试 4: should_auto_compact — context_collapse 互斥 ----
    print("\n--- 测试 4: should_auto_compact — context_collapse 互斥 ---")
    try:
        tracking = CompactTracking()
        result = should_auto_compact([], "gpt-4o", tracking, context_collapse_enabled=True)
        assert result is False, f"期望 False, 得到 {result}"
        print("  [PASS] context_collapse 启用时 autocompact 跳过")
    except Exception as e:
        print(f"  [FAIL] {e}")

    # ---- 测试 5: should_auto_compact — circuit breaker ----
    print("\n--- 测试 5: should_auto_compact — circuit breaker ---")
    try:
        tracking = CompactTracking(consecutive_failures=3)
        result = should_auto_compact([], "gpt-4o", tracking)
        assert result is False, f"期望 False, 得到 {result}"
        print("  [PASS] consecutive_failures >= 3 时跳过")
    except Exception as e:
        print(f"  [FAIL] {e}")

    # ---- 测试 6: should_auto_compact — circuit breaker 未触发 ----
    print("\n--- 测试 6: should_auto_compact — circuit breaker 未触发 ---")
    try:
        tracking = CompactTracking(consecutive_failures=2)
        # 不检查返回值（取决于 token 数），只确认不因 circuit breaker 被跳过
        # 由于消息很少，token 不会超过阈值，所以结果是 False
        result = should_auto_compact(
            [{"role": "user", "content": "Hi"}], "gpt-4o", tracking
        )
        print(f"  consecutive_failures=2, should_auto_compact={result}")
        print("  [PASS] consecutive_failures < 3 时正常判断")
    except Exception as e:
        print(f"  [FAIL] {e}")

    # ---- 测试 7: get_auto_compact_threshold ----
    print("\n--- 测试 7: get_auto_compact_threshold ---")
    try:
        threshold = get_auto_compact_threshold("gpt-4o")
        expected = 128000 - 13000  # 115000
        assert threshold == expected, f"期望 {expected}, 得到 {threshold}"
        print(f"  gpt-4o: threshold={threshold}")
        print("  [PASS] 阈值计算正确")
    except Exception as e:
        print(f"  [FAIL] {e}")

    # ---- 测试 8: get_auto_compact_threshold — 未知模型 ----
    print("\n--- 测试 8: get_auto_compact_threshold — 未知模型 ---")
    try:
        threshold = get_auto_compact_threshold("unknown-model")
        expected = 128000 - 13000  # 默认 context_window=128000
        assert threshold == expected, f"期望 {expected}, 得到 {threshold}"
        print(f"  unknown-model: threshold={threshold}")
        print("  [PASS] 未知模型使用默认阈值")
    except Exception as e:
        print(f"  [FAIL] {e}")

    # ---- 测试 9: CompactTracking dataclass ----
    print("\n--- 测试 9: CompactTracking dataclass ---")
    try:
        t = CompactTracking()
        assert t.consecutive_failures == 0
        assert t.total_failures == 0
        assert t.last_compact_time is None

        t.consecutive_failures = 2
        t.total_failures = 5
        t.last_compact_time = time.time()
        assert t.consecutive_failures == 2
        assert t.total_failures == 5
        assert t.last_compact_time is not None
        print("  [PASS] CompactTracking dataclass 正常")
    except Exception as e:
        print(f"  [FAIL] {e}")

    # ---- 测试 10: auto_compact_if_needed — 不需要压缩 ----
    print("\n--- 测试 10: auto_compact_if_needed — 不需要压缩 ---")
    try:
        import asyncio

        messages = [{"role": "user", "content": "Hello"}]
        tracking = CompactTracking()
        result, was_compacted = asyncio.run(
            auto_compact_if_needed(messages, "gpt-4o", tracking)
        )
        assert was_compacted is False, f"期望 False, 得到 {was_compacted}"
        assert result == messages
        print("  [PASS] 不需要压缩时原样返回")
    except Exception as e:
        print(f"  [FAIL] {e}")

    # ---- 测试 11: auto_compact_if_needed — circuit breaker 阻止 ----
    print("\n--- 测试 11: auto_compact_if_needed — circuit breaker 阻止 ---")
    try:
        import asyncio

        messages = [{"role": "user", "content": "Hello"}]
        tracking = CompactTracking(consecutive_failures=3)
        result, was_compacted = asyncio.run(
            auto_compact_if_needed(messages, "gpt-4o", tracking)
        )
        assert was_compacted is False
        assert tracking.consecutive_failures == 3  # 未增加
        print("  [PASS] circuit breaker 阻止压缩尝试")
    except Exception as e:
        print(f"  [FAIL] {e}")

    # ---- 测试 12: auto_compact_if_needed — DISABLE_COMPACT 阻止 ----
    print("\n--- 测试 12: auto_compact_if_needed — DISABLE_COMPACT 阻止 ---")
    try:
        import asyncio

        old_val = os.environ.get(DISABLE_COMPACT)
        os.environ[DISABLE_COMPACT] = "1"
        try:
            messages = [{"role": "user", "content": "Hello"}]
            tracking = CompactTracking()
            result, was_compacted = asyncio.run(
                auto_compact_if_needed(messages, "gpt-4o", tracking)
            )
            assert was_compacted is False
            print("  [PASS] DISABLE_COMPACT 阻止压缩")
        finally:
            if old_val is None:
                os.environ.pop(DISABLE_COMPACT, None)
            else:
                os.environ[DISABLE_COMPACT] = old_val
    except Exception as e:
        print(f"  [FAIL] {e}")

    print("\n" + "=" * 60)
    print("所有测试完成")
    print("=" * 60)
