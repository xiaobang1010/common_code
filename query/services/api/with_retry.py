"""带重试的 API 调用封装模块。

提供指数退避重试逻辑，支持 retry-after header、
可配置的重试错误类型和最大重试次数。
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, TypeVar

from query.services.api.errors import APIError, classify_error

T = TypeVar("T")


# ---------------------------------------------------------------------------
# RetryConfig dataclass
# ---------------------------------------------------------------------------


@dataclass
class RetryConfig:
    """重试配置。

    Attributes:
        max_retries: 最大重试次数
        base_delay: 基础延迟（秒）
        max_delay: 最大延迟（秒）
        retryable_errors: 可重试的错误类型集合
    """

    max_retries: int = 3
    base_delay: float = 1.0
    max_delay: float = 60.0
    retryable_errors: set[str] = field(
        default_factory=lambda: {"rate_limit", "server_error"}
    )


# ---------------------------------------------------------------------------
# with_retry — 带指数退避重试的函数包装器
# ---------------------------------------------------------------------------


async def with_retry(
    fn: Callable[[], Coroutine[Any, Any, T]],
    retry_config: RetryConfig | None = None,
) -> T:
    """带指数退避重试的异步函数包装器。

    仅对 retryable_errors 中的错误类型重试。
    指数退避：delay = min(base_delay * 2^attempt, max_delay) + jitter
    rate_limit 错误使用 retry-after header（如有）。
    超过最大重试次数后抛出最后一次异常。

    Args:
        fn: 要执行的异步函数（无参数）
        retry_config: 重试配置，为 None 时使用默认配置

    Returns:
        fn 的返回值

    Raises:
        Exception: 超过最大重试次数后抛出最后一次异常
    """
    config = retry_config or RetryConfig()
    last_error: Exception | None = None

    for attempt in range(config.max_retries + 1):
        try:
            return await fn()
        except Exception as error:
            last_error = error
            api_error = classify_error(error)

            # 不在可重试集合中，直接抛出
            if api_error.type not in config.retryable_errors:
                raise

            # 已达最大重试次数，抛出
            if attempt >= config.max_retries:
                raise

            # 计算延迟
            delay = _calculate_delay(
                attempt=attempt,
                base_delay=config.base_delay,
                max_delay=config.max_delay,
                api_error=api_error,
            )

            await asyncio.sleep(delay)

    # 理论上不会到达这里，但类型检查需要
    raise last_error  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _calculate_delay(
    attempt: int,
    base_delay: float,
    max_delay: float,
    api_error: APIError,
) -> float:
    """计算重试延迟。

    策略：
      - rate_limit 错误优先使用 retry-after header
      - 否则使用指数退避：min(base_delay * 2^attempt, max_delay)
      - 添加随机 jitter（0~25%）
    """
    # rate_limit 错误优先使用 retry-after
    if api_error.type == "rate_limit" and api_error.retry_after is not None:
        return min(api_error.retry_after, max_delay)

    # 指数退避
    exponential_delay = base_delay * (2 ** attempt)
    capped_delay = min(exponential_delay, max_delay)

    # 添加 jitter（0~25%）
    jitter = random.random() * 0.25 * capped_delay
    return capped_delay + jitter


# ---------------------------------------------------------------------------
# 测试块
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import httpx
    import time
    from unittest.mock import AsyncMock

    import openai

    def _make_response(status_code: int = 400) -> httpx.Response:
        """构造用于 openai SDK 错误的 httpx.Response。"""
        return httpx.Response(
            status_code=status_code,
            request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions"),
        )

    print("=" * 60)
    print("重试逻辑测试")
    print("=" * 60)

    # ---- 测试 1: 成功调用（无重试） ----
    print("\n--- 测试 1: 成功调用（无重试） ---")
    try:
        mock_fn = AsyncMock(return_value="success")

        async def test_success():
            result = await with_retry(mock_fn)
            assert result == "success", f"期望 'success', 得到 {result}"
            assert mock_fn.call_count == 1, f"期望调用 1 次, 实际 {mock_fn.call_count} 次"

        asyncio.run(test_success())
        print("  [PASS] 成功调用无重试")
    except Exception as e:
        print(f"  [FAIL] {e}")

    # ---- 测试 2: 可重试错误 — 重试后成功 ----
    print("\n--- 测试 2: 可重试错误 — 重试后成功 ---")
    try:
        call_count = [0]

        async def flaky_fn():
            call_count[0] += 1
            if call_count[0] < 3:
                raise openai.RateLimitError(
                    message="Rate limit",
                    response=_make_response(429),
                    body=None,
                )
            return "recovered"

        async def test_retry_success():
            config = RetryConfig(max_retries=3, base_delay=0.01, max_delay=0.1)
            result = await with_retry(flaky_fn, config)
            assert result == "recovered", f"期望 'recovered', 得到 {result}"
            assert call_count[0] == 3, f"期望调用 3 次, 实际 {call_count[0]} 次"

        asyncio.run(test_retry_success())
        print("  [PASS] 重试后成功")
    except Exception as e:
        print(f"  [FAIL] {e}")

    # ---- 测试 3: 不可重试错误 — 立即抛出 ----
    print("\n--- 测试 3: 不可重试错误 — 立即抛出 ---")
    try:
        call_count = [0]

        async def auth_fail_fn():
            call_count[0] += 1
            raise openai.AuthenticationError(
                message="Invalid API key",
                response=_make_response(401),
                body=None,
            )

        async def test_no_retry():
            config = RetryConfig(max_retries=3, base_delay=0.01)
            try:
                await with_retry(auth_fail_fn, config)
                assert False, "应该抛出异常"
            except openai.AuthenticationError:
                pass
            assert call_count[0] == 1, f"期望调用 1 次, 实际 {call_count[0]} 次"

        asyncio.run(test_no_retry())
        print("  [PASS] 不可重试错误立即抛出")
    except Exception as e:
        print(f"  [FAIL] {e}")

    # ---- 测试 4: 超过最大重试次数 — 抛出最后一次异常 ----
    print("\n--- 测试 4: 超过最大重试次数 ---")
    try:
        call_count = [0]

        async def always_fail_fn():
            call_count[0] += 1
            raise openai.InternalServerError(
                message="Server error",
                response=_make_response(500),
                body=None,
            )

        async def test_max_retries():
            config = RetryConfig(max_retries=2, base_delay=0.01, max_delay=0.1)
            try:
                await with_retry(always_fail_fn, config)
                assert False, "应该抛出异常"
            except openai.InternalServerError:
                pass
            # 初始调用 + 2 次重试 = 3 次
            assert call_count[0] == 3, f"期望调用 3 次, 实际 {call_count[0]} 次"

        asyncio.run(test_max_retries())
        print("  [PASS] 超过最大重试次数抛出异常")
    except Exception as e:
        print(f"  [FAIL] {e}")

    # ---- 测试 5: 指数退避延迟 ----
    print("\n--- 测试 5: 指数退避延迟 ---")
    try:
        config = RetryConfig(base_delay=1.0, max_delay=60.0)

        # attempt=0: 1.0 + jitter
        d0 = _calculate_delay(0, 1.0, 60.0, APIError(type="server_error", message=""))
        assert 1.0 <= d0 <= 1.25, f"attempt=0 延迟超出范围: {d0}"

        # attempt=1: 2.0 + jitter
        d1 = _calculate_delay(1, 1.0, 60.0, APIError(type="server_error", message=""))
        assert 2.0 <= d1 <= 2.5, f"attempt=1 延迟超出范围: {d1}"

        # attempt=2: 4.0 + jitter
        d2 = _calculate_delay(2, 1.0, 60.0, APIError(type="server_error", message=""))
        assert 4.0 <= d2 <= 5.0, f"attempt=2 延迟超出范围: {d2}"

        # max_delay 封顶
        d_big = _calculate_delay(10, 1.0, 8.0, APIError(type="server_error", message=""))
        assert 8.0 <= d_big <= 10.0, f"大 attempt 延迟应被 max_delay 封顶: {d_big}"

        print(f"  attempt=0: {d0:.3f}s (期望 ~1.0)")
        print(f"  attempt=1: {d1:.3f}s (期望 ~2.0)")
        print(f"  attempt=2: {d2:.3f}s (期望 ~4.0)")
        print(f"  attempt=10 (capped): {d_big:.3f}s (期望 ~8.0)")
        print("  [PASS] 指数退避延迟")
    except Exception as e:
        print(f"  [FAIL] {e}")

    # ---- 测试 6: rate_limit 使用 retry-after ----
    print("\n--- 测试 6: rate_limit 使用 retry-after ---")
    try:
        api_err = APIError(type="rate_limit", message="", retry_after=5.0)
        delay = _calculate_delay(0, 1.0, 60.0, api_err)
        assert delay == 5.0, f"期望 5.0, 得到 {delay}"

        # retry-after 超过 max_delay 时应封顶
        api_err_big = APIError(type="rate_limit", message="", retry_after=120.0)
        delay_big = _calculate_delay(0, 1.0, 60.0, api_err_big)
        assert delay_big == 60.0, f"期望 60.0 (封顶), 得到 {delay_big}"

        print(f"  retry_after=5.0 → delay={delay}s")
        print(f"  retry_after=120.0 (capped) → delay={delay_big}s")
        print("  [PASS] rate_limit 使用 retry-after")
    except Exception as e:
        print(f"  [FAIL] {e}")

    # ---- 测试 7: 自定义 retryable_errors ----
    print("\n--- 测试 7: 自定义 retryable_errors ---")
    try:
        call_count = [0]

        async def context_fail_fn():
            call_count[0] += 1
            raise openai.BadRequestError(
                message="context_length_exceeded",
                response=_make_response(400),
                body=None,
            )

        async def test_custom_retryable():
            config = RetryConfig(
                max_retries=2,
                base_delay=0.01,
                retryable_errors={"context_length_exceeded"},
            )
            try:
                await with_retry(context_fail_fn, config)
                assert False, "应该抛出异常"
            except openai.BadRequestError:
                pass
            # 初始调用 + 2 次重试 = 3 次
            assert call_count[0] == 3, f"期望调用 3 次, 实际 {call_count[0]} 次"

        asyncio.run(test_custom_retryable())
        print("  [PASS] 自定义 retryable_errors")
    except Exception as e:
        print(f"  [FAIL] {e}")

    # ---- 测试 8: 默认配置不重试 context_length_exceeded ----
    print("\n--- 测试 8: 默认配置不重试 context_length_exceeded ---")
    try:
        call_count = [0]

        async def context_fail_fn2():
            call_count[0] += 1
            raise openai.BadRequestError(
                message="context_length_exceeded",
                response=_make_response(400),
                body=None,
            )

        async def test_default_no_retry():
            config = RetryConfig(max_retries=3, base_delay=0.01)
            try:
                await with_retry(context_fail_fn2, config)
                assert False, "应该抛出异常"
            except openai.BadRequestError:
                pass
            assert call_count[0] == 1, f"期望调用 1 次, 实际 {call_count[0]} 次"

        asyncio.run(test_default_no_retry())
        print("  [PASS] 默认配置不重试 context_length_exceeded")
    except Exception as e:
        print(f"  [FAIL] {e}")

    print("\n" + "=" * 60)
    print("所有测试完成")
    print("=" * 60)
