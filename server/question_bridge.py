"""提问请求挂起器 - 把 AskUserQuestion 工具的提问桥接到 HTTP 接口。

模型调用 AskUserQuestion 工具时，QuestionBridge 创建一个 Future 挂起等待，
同时把问题信息登记进未决表（状态查询式）供 SSE 生成器轮询推送。
前端收到 question_request 事件后，POST /api/question 回传回答，
QuestionBridge.resolve 解除 Future 挂起，回答文本作为工具结果返回模型。

并发语义同 PermissionBridge：未决表状态查询（多流可见）、
清理按来源收窄（任务收尾只清自己的请求）。
"""

from __future__ import annotations

import asyncio
from uuid import uuid4


class QuestionBridge:
    """提问请求挂起器，在工具和 HTTP 前端之间传递用户回答。"""

    def __init__(self) -> None:
        # request_id -> Future，存等待回答的挂起请求
        self._pending: dict[str, asyncio.Future[str]] = {}
        # request_id -> 请求事件元数据（未决表，状态查询式）
        self._pending_meta: dict[str, dict] = {}

    async def ask_question(
        self,
        question: str,
        options: list[dict],
        session_id: str = "",
    ) -> str:
        """工具调用的提问回调，挂起等待前端回答。

        Args:
            question: 问题文本
            options: 候选选项列表，每项含 label / description
            session_id: 来源会话 id（后台任务标注，跨会话弹窗用）

        Returns:
            用户的回答文本
        """
        request_id = str(uuid4())
        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()
        self._pending[request_id] = future
        self._pending_meta[request_id] = {
            "type": "question_request",
            "request_id": request_id,
            "question": question,
            "options": options,
            "session_id": session_id,
        }

        # 挂起等待前端回传回答
        answer = await future
        return answer

    def resolve(self, request_id: str, answer: str) -> bool:
        """前端回传回答，解除对应请求的挂起。

        Args:
            request_id: 请求 ID
            answer: 用户的回答文本

        Returns:
            True 表示找到并解除了挂起，False 表示请求不存在
        """
        future = self._pending.pop(request_id, None)
        self._pending_meta.pop(request_id, None)
        if future is None:
            return False
        if not future.done():
            future.set_result(answer)
        return True

    def get_pending_questions(self) -> list[dict]:
        """返回全部未决提问请求（状态查询式，非消费）。"""
        return list(self._pending_meta.values())

    def clear_pending(self, session_id: str | None = None) -> None:
        """清理挂起请求与未决表。

        Args:
            session_id: 给定时只清该会话发出的请求；不给定时清全部
        """
        target_ids = [
            rid
            for rid, meta in self._pending_meta.items()
            if session_id is None or meta.get("session_id") == session_id
        ]
        for rid in target_ids:
            future = self._pending.pop(rid, None)
            self._pending_meta.pop(rid, None)
            if future is not None and not future.done():
                future.cancel()
