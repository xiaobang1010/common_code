"""Inbox 轮询器 — teammate 消息拾取。

每秒轮询 teammate 自己的 inbox，收到消息后作为新对话轮次注入
（包装为 user 消息）。忙时排队，轮次结束后投递。
结构化协议消息（shutdown_request 等）路由到专门处理。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from tools.team.mailbox import read_inbox, is_structured_protocol_message

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

INBOX_POLL_INTERVAL = 1.0  # 秒


# ---------------------------------------------------------------------------
# InboxPoller — 邮箱轮询器
# ---------------------------------------------------------------------------


class InboxPoller:
    """teammate 的 inbox 轮询器。

    后台 asyncio.Task 每秒轮询 inbox，收到消息后：
    - 普通消息 → 调 on_message 回调（注入对话轮次）
    - 结构化协议消息 → 调 on_protocol 回调（如 shutdown 处理）

    忙时（is_busy=True）消息排队，轮次结束后投递。
    """

    def __init__(
        self,
        team_name: str,
        agent_name: str,
        on_message: Callable[[dict], Any] | None = None,
        on_protocol: Callable[[dict], Any] | None = None,
    ) -> None:
        self.team_name = team_name
        self.agent_name = agent_name
        self._on_message = on_message
        self._on_protocol = on_protocol
        self._task: asyncio.Task | None = None
        self._running = False
        self._busy = False  # 是否正在处理一轮对话
        self._pending: list[dict] = []  # 忙时排队的消息
        self._shutdown = False

    # ------------------------------------------------------------------
    # 启动 / 停止
    # ------------------------------------------------------------------

    def start(self) -> None:
        """启动轮询。"""
        if self._task is not None:
            return
        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        logger.debug("Inbox 轮询器启动: %s/%s", self.team_name, self.agent_name)

    async def stop(self) -> None:
        """停止轮询。"""
        self._running = False
        self._shutdown = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.debug("Inbox 轮询器停止: %s/%s", self.team_name, self.agent_name)

    # ------------------------------------------------------------------
    # 忙碌状态
    # ------------------------------------------------------------------

    def set_busy(self, busy: bool) -> None:
        """设置忙碌状态。

        忙碌时消息排队，解除忙碌时投递排队的消息。
        """
        self._busy = busy
        if not busy:
            # 解除忙碌，投递排队消息
            self._flush_pending()

    def _flush_pending(self) -> None:
        """投递排队的消息。"""
        pending = list(self._pending)
        self._pending.clear()
        for msg in pending:
            self._dispatch(msg)

    # ------------------------------------------------------------------
    # 轮询循环
    # ------------------------------------------------------------------

    async def _poll_loop(self) -> None:
        """后台轮询循环。"""
        while self._running and not self._shutdown:
            try:
                messages = read_inbox(self.team_name, self.agent_name)
                for msg in messages:
                    if self._busy:
                        # 忙时排队
                        self._pending.append(msg)
                    else:
                        self._dispatch(msg)
            except Exception as e:
                logger.warning("Inbox 轮询出错: %s", e)

            await asyncio.sleep(INBOX_POLL_INTERVAL)

    def _dispatch(self, msg: dict) -> None:
        """分发消息到对应回调。"""
        if is_structured_protocol_message(msg):
            # 结构化协议消息
            logger.info(
                "收到协议消息: %s (from %s)",
                msg.get("msg_type"), msg.get("from"),
            )
            if self._on_protocol is not None:
                try:
                    self._on_protocol(msg)
                except Exception as e:
                    logger.exception("协议消息处理异常: %s", e)
        else:
            # 普通消息
            logger.debug(
                "收到消息 from %s: %s",
                msg.get("from"), msg.get("summary", "")[:50],
            )
            if self._on_message is not None:
                try:
                    self._on_message(msg)
                except Exception as e:
                    logger.exception("消息处理异常: %s", e)
