"""事件循环阻塞监控。

以环境变量 CC_LOOP_MONITOR=1 开启（默认关闭）。心跳协程期望每 100ms
醒来一次，实际间隔超过阈值（默认 1s）说明事件循环被同步调用冻住，
恢复后向 stderr 输出本次阻塞时长——阻塞期间协程自身也被冻住，
只能在恢复后告警，用于开发期的阻塞基线测量与回归检测。
"""

from __future__ import annotations

import asyncio
import os
import sys
import time

# 期望心跳间隔与告警阈值（秒）
_TICK_INTERVAL = 0.1
_BLOCK_THRESHOLD = 1.0


def is_monitor_enabled() -> bool:
    """监控开关：CC_LOOP_MONITOR=1/true/yes 时开启，默认关闭。"""
    return os.environ.get("CC_LOOP_MONITOR", "").strip().lower() in ("1", "true", "yes")


async def _monitor_loop() -> None:
    """心跳协程：测量相邻两次醒来的间隔，超阈值即输出阻塞毫秒数。"""
    last = time.monotonic()
    while True:
        await asyncio.sleep(_TICK_INTERVAL)
        now = time.monotonic()
        gap = now - last
        last = now
        if gap > _BLOCK_THRESHOLD:
            sys.stderr.write(f"[loop-monitor] 事件循环阻塞 {int(gap * 1000)}ms\n")
            sys.stderr.flush()


def start_loop_monitor() -> asyncio.Task | None:
    """在当前事件循环上挂载监控协程；开关关闭时返回 None。"""
    if not is_monitor_enabled():
        return None
    return asyncio.ensure_future(_monitor_loop())
