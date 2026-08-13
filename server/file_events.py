"""工作区文件变更事件 broker — 供 AI 写盘后经 SSE 推送前端。

与 chat 路由的心跳模式对齐：订阅者各自持有一条队列，实现真广播；
工作区外（additional_directories 白名单）的写入会被过滤，不下发。
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from server.paths import project_root


class FileEventBroker:
    """文件变更事件广播器：每个订阅者一条独立队列，避免单队列被单个消费者取走。"""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        """注册一个订阅者，返回其专属队列。"""
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        """注销订阅者。"""
        self._subscribers.discard(queue)

    def publish(self, event: dict[str, Any]) -> None:
        """向所有订阅者广播事件。"""
        for queue in list(self._subscribers):
            queue.put_nowait(event)


# 全局单例
file_event_broker = FileEventBroker()


def notify_file_changed(absolute_path: str, change_type: str, mtime: int, size: int) -> None:
    """AI 工具写盘成功后调用，推送 file_changed 事件。

    计算相对工作区的路径；工作区外（如 additional_directories 白名单目录）
    的写入会被过滤，不推送给前端（前端文件树不存在该路径）。
    """
    root = os.path.realpath(project_root())
    resolved = os.path.realpath(absolute_path)
    try:
        rel = os.path.relpath(resolved, root).replace("\\", "/")
    except ValueError:
        # 跨驱动器等异常，忽略
        return
    if rel.startswith(".."):
        # 工作区外的写入，不下发
        return
    file_event_broker.publish(
        {
            "type": "file_changed",
            "path": rel,
            "change_type": change_type,
            "mtime": mtime,
            "size": size,
        }
    )
