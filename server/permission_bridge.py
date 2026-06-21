"""权限请求挂起器 — 把引擎的权限回调桥接到 HTTP 接口。

引擎调用 permission_prompt 回调时，PermissionBridge 创建一个 Future 挂起等待，
同时把请求信息放入队列供 SSE 生成器轮询推送。
前端收到 permission_request 事件后，POST /api/permission 回传决策，
PermissionBridge.resolve 解除 Future 挂起，引擎继续执行。
"""

from __future__ import annotations

import asyncio
from uuid import uuid4


class PermissionBridge:
    """权限请求挂起器，在引擎和 HTTP 前端之间传递权限决策。"""

    def __init__(self) -> None:
        # request_id -> Future，存等待决策的挂起请求
        self._pending: dict[str, asyncio.Future[str]] = {}
        # 待推送队列，SSE 生成器从这里取权限请求事件推给前端
        self._pending_requests: asyncio.Queue[dict] = asyncio.Queue()

    async def request_permission(
        self,
        tool_name: str,
        tool_input: dict,
        reason: str,
    ) -> str:
        """引擎调用的权限回调，挂起等待前端决策。

        引擎在工具调用前调这个方法，方法内部创建 Future 挂起，
        把请求信息放入待推送队列，等前端 POST /api/permission 回传决策后解除挂起。

        Args:
            tool_name: 工具名称
            tool_input: 工具输入参数
            reason: 请求权限的原因

        Returns:
            决策字符串："allow" / "deny" / "always_allow"
        """
        request_id = str(uuid4())
        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()
        self._pending[request_id] = future

        # 放入待推送队列，SSE 生成器会取出来推给前端
        await self._pending_requests.put({
            "type": "permission_request",
            "request_id": request_id,
            "tool_name": tool_name,
            "tool_input": tool_input,
            "reason": reason,
        })

        # 挂起等待前端回传决策
        decision = await future
        return decision

    def resolve(self, request_id: str, decision: str) -> bool:
        """前端回传决策，解除对应请求的挂起。

        Args:
            request_id: 请求 ID
            decision: 决策字符串："allow" / "deny" / "always_allow"

        Returns:
            True 表示找到并解除了挂起，False 表示请求不存在
        """
        future = self._pending.pop(request_id, None)
        if future is None:
            return False
        if not future.done():
            future.set_result(decision)
        return True

    def get_pending_permission_request(self) -> dict | None:
        """非阻塞地从队列取一个待推送的权限请求。

        SSE 生成器在引擎事件间隙调这个方法，检查有没有权限请求需要推给前端。

        Returns:
            权限请求字典 {"type":"permission_request","request_id":...,"tool_name":...,"tool_input":...,"reason":...}，
            或 None 表示没有待推送的请求
        """
        try:
            return self._pending_requests.get_nowait()
        except asyncio.QueueEmpty:
            return None
