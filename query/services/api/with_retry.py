"""带重试的 API 调用封装模块。

提供指数退避重试逻辑，支持 retry-after header、
可配置的重试错误类型和最大重试次数。
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Callable, Coroutine, TypeVar

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

    max_retries: int = 10
    base_delay: float = 0.5
    max_delay: float = 32.0
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
# with_retry_stream — 带重试的 async generator 包装器
# ---------------------------------------------------------------------------


async def with_retry_stream(
    fn: Callable[[], AsyncGenerator[T, None]],
    retry_config: RetryConfig | None = None,
) -> AsyncGenerator[T, None]:
    """带重试的 async generator 包装器。

    仅在首次 yield 前重试（建立阶段）。一旦开始 yield，
    不再重试（避免重复输出）。

    重试逻辑：
      - 调用 fn() 获取 async generator
      - 尝试获取首个事件（await gen.__anext__()）
      - 首个事件获取失败（抛异常）→ 判断是否可重试 → 重试
      - 首个事件获取成功 → 重试窗口关闭，正常迭代剩余事件
      - 迭代中失败 → 不重试，异常冒泡
      - StopAsyncIteration（空 generator）→ 正常结束，不重试

    Args:
        fn: 返回 async generator 的工厂函数（无参数）
        retry_config: 重试配置，为 None 时使用默认配置

    Yields:
        fn 产出的所有事件
    """
    config = retry_config or RetryConfig()
    last_error: Exception | None = None

    for attempt in range(config.max_retries + 1):
        gen = fn()
        try:
            # 尝试获取首个事件——这是重试窗口
            first_event = await gen.__anext__()
        except StopAsyncIteration:
            # 空 generator，正常结束
            return
        except Exception as error:
            last_error = error
            api_error = classify_error(error)

            # 不在可重试集合中，直接抛出
            if api_error.type not in config.retryable_errors:
                raise

            # 已达最大重试次数，抛出
            if attempt >= config.max_retries:
                raise

            # 计算延迟并等待
            delay = _calculate_delay(
                attempt=attempt,
                base_delay=config.base_delay,
                max_delay=config.max_delay,
                api_error=api_error,
            )
            await asyncio.sleep(delay)
            continue

        # 首个事件获取成功，重试窗口关闭
        yield first_event

        # 继续迭代剩余事件——这里不再重试
        async for event in gen:
            yield event
        return

    # 理论上不会到达这里
    if last_error:
        raise last_error


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
