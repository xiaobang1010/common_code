"""事件循环阻塞监控模块测试。

在线程外注入同步阻塞，断言心跳协程恢复后向 stderr 输出阻塞时长；
以及开关关闭时不启动协程。
"""

from __future__ import annotations

import asyncio
import time

import pytest

from server import loop_monitor


@pytest.mark.parametrize("value", ["1", "true", "YES"])
def test_enabled(monkeypatch, value):
    monkeypatch.setenv("CC_LOOP_MONITOR", value)
    assert loop_monitor.is_monitor_enabled() is True


@pytest.mark.parametrize("value", ["", "0", "off", None])
def test_disabled_by_default(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("CC_LOOP_MONITOR", raising=False)
    else:
        monkeypatch.setenv("CC_LOOP_MONITOR", value)
    assert loop_monitor.is_monitor_enabled() is False


def test_disabled_returns_none(monkeypatch):
    monkeypatch.delenv("CC_LOOP_MONITOR", raising=False)
    async def run():
        return loop_monitor.start_loop_monitor()
    assert asyncio.run(run()) is None


def test_blocked_loop_reports_to_stderr(monkeypatch, capsys):
    """人为阻塞事件循环超阈值，恢复后 stderr 输出阻塞毫秒数。"""
    monkeypatch.setenv("CC_LOOP_MONITOR", "1")

    async def run():
        task = loop_monitor.start_loop_monitor()
        assert task is not None
        try:
            # 先让协程跑起来记下基准时间
            await asyncio.sleep(0.05)
            # 冻住事件循环 1.2s（超过 1s 告警阈值）
            time.sleep(1.2)
            await asyncio.sleep(0.3)
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    asyncio.run(run())
    out = capsys.readouterr().err
    assert "[loop-monitor]" in out
    assert "事件循环阻塞" in out
    # 输出的阻塞毫秒数应不低于冻结时长（1200ms，留误差余量）
    ms = int(out.split("事件循环阻塞")[1].split("ms")[0])
    assert ms >= 1100
