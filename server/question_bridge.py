"""提问请求挂起器 — 把 AskUserQuestion 工具的提问桥接到 HTTP 接口。

模型调用 AskUserQuestion 工具时，QuestionBridge 创建一个 Future 挂起等待，
同时把问题信息放入队列供 SSE 生成器轮询推送。
前端收到 question_request 事件后，POST /api/question 回传回答，
QuestionBridge.resolve 解除 Future 挂起，回答文本作为工具结果返回给模型。
"""

from __future__ import annotations

import asyncio
from uuid import uuid4


class QuestionBridge:
    """提问请求挂起器，在工具和 HTTP 前端之间传递用户回答。"""

    def __init__(self) -> None:
        # request_id -> Future，存等待回答的挂起请求
        self._pending: dict[str, asyncio.Future[str]] = {}
        # 待推送队列，SSE 生成器从这里取提问事件推给前端
        self._pending_questions: asyncio.Queue[dict] = asyncio.Queue()

    async def ask_question(
        self,
        question: str,
        options: list[dict],
    ) -> str:
        """工具调用的提问回调，挂起等待前端回答。

        Args:
            question: 问题文本
            options: 候选选项列表，每项含 label / description

        Returns:
            用户的回答文本
        """
        request_id = str(uuid4())
        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()
        self._pending[request_id] = future

        # 放入待推送队列，SSE 生成器会取出来推给前端
        await self._pending_questions.put({
            "type": "question_request",
            "request_id": request_id,
            "question": question,
            "options": options,
        })

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
        if future is None:
            return False
        if not future.done():
            future.set_result(answer)
        return True

    def get_pending_question(self) -> dict | None:
        """非阻塞地从队列取一个待推送的提问请求。

        SSE 生成器在引擎事件间隙调这个方法，检查有没有提问需要推给前端。

        Returns:
            提问请求字典 {"type":"question_request","request_id":...,"question":...,"options":...}，
            或 None 表示没有待推送的提问
        """
        try:
            return self._pending_questions.get_nowait()
        except asyncio.QueueEmpty:
            return None
