"""流式调用超时看护测试：首事件 wait_for 快速失败、失败生成器关闭、超时归类可重试。

背景：双任务并行时供应商/代理可能挂流（服务器接受请求后长时间不吐首个
chunk），原实现要等 httpx 读超时（600 秒）才有第一次反馈，用户界面
长时间停留在「等待模型响应」。修复：with_retry_stream 给首事件加
first_event_timeout 看护，超时按可重试网络错误处理；读超时收紧到 120 秒。
"""

from __future__ import annotations

import asyncio

import pytest

from query.services.api.errors import classify_error
from query.services.api.with_retry import RetryConfig, with_retry_stream


def _hung_gen(closed_flag: dict):
    """首包永久挂起的生成器（模拟服务器不吐 chunk），关闭时记标志。"""

    async def gen():
        try:
            await asyncio.sleep(30)
            yield "never"
        finally:
            closed_flag["closed"] = True

    return gen()


def _ok_gen(events: list):
    """立即产出全部事件的正常生成器。"""

    async def gen():
        for e in events:
            yield e

    return gen()


# --- 首事件看护超时 ---


@pytest.mark.asyncio
async def test_first_event_timeout_then_retry_success():
    """首包挂起：看护超时后重试，第二次成功产出全部事件。"""
    attempts: list[int] = []
    closed_flags: list[dict] = [{"closed": False}, {"closed": False}]

    def factory():
        attempts.append(1)
        # 第一次挂起，第二次正常
        return _hung_gen(closed_flags[0]) if len(attempts) == 1 else _ok_gen(["a", "b"])

    events = []
    async for ev in with_retry_stream(
        factory,
        RetryConfig(max_retries=2, base_delay=0.01, max_delay=0.01),
        first_event_timeout=0.2,
    ):
        events.append(ev)

    assert events == ["a", "b"]
    assert len(attempts) == 2
    assert closed_flags[0]["closed"] is True  # 挂起生成器已释放


@pytest.mark.asyncio
async def test_retry_notice_event_yieldd():
    """重试时回调返回的提示事件随流透出（避免重试全程静默）。"""
    calls: list[tuple[int, int]] = []

    async def on_retry(n: int, total: int, error: Exception):
        calls.append((n, total))
        return f"notice-{n}"

    def factory():
        return _hung_gen({"closed": False})

    events = []
    with pytest.raises(TimeoutError):
        async for ev in with_retry_stream(
            factory,
            RetryConfig(max_retries=1, base_delay=0.01, max_delay=0.01),
            first_event_timeout=0.1,
            on_retry=on_retry,
        ):
            events.append(ev)

    # 重试前提示事件先于（可能的）后续事件产出；耗尽后抛 TimeoutError
    assert events == ["notice-1"]
    assert calls == [(1, 1)]


@pytest.mark.asyncio
async def test_first_event_timeout_retries_exhausted():
    """持续挂起：重试耗尽后抛出看护超时异常（不再干等读超时）。"""
    attempts: list[int] = []

    def factory():
        attempts.append(1)
        return _hung_gen({"closed": False})

    with pytest.raises(TimeoutError):
        async for _ in with_retry_stream(
            factory,
            RetryConfig(max_retries=1, base_delay=0.01, max_delay=0.01),
            first_event_timeout=0.2,
        ):
            pass

    assert len(attempts) == 2  # 初次 + 重试一次


@pytest.mark.asyncio
async def test_first_event_within_timeout_no_retry():
    """首包在窗口内到达：正常产出，不触发看护。"""
    attempts: list[int] = []

    def factory():
        attempts.append(1)

        async def gen():
            await asyncio.sleep(0.01)
            yield "x"

        return gen()

    events = [ev async for ev in with_retry_stream(factory, first_event_timeout=5.0)]
    assert events == ["x"]
    assert len(attempts) == 1


# --- 超时错误归类 ---


def test_timeout_error_classified_retryable():
    """看护超时（TimeoutError）归类为可重试的 server_error。"""
    result = classify_error(TimeoutError())
    assert result.type == "server_error"


def test_asyncio_timeout_error_classified_retryable():
    """asyncio.TimeoutError（3.11+ 与内建同体）同样可重试。"""
    result = classify_error(asyncio.TimeoutError())
    assert result.type == "server_error"
