"""权限请求挂起器 - 把引擎的权限回调桥接到 HTTP 接口。

引擎调用 permission_prompt 回调时，PermissionBridge 创建一个 Future 挂起等待，
同时把请求信息登记进未决表（状态查询式）供 SSE 生成器轮询推送。
前端收到 permission_request 事件后，POST /api/permission 回传决策，
PermissionBridge.resolve 解除 Future 挂起，引擎继续执行。

并发语义（后台任务模型）：
- 未决表是状态查询而非消费队列：多个 SSE 流都能看到全部未决请求，
  后台任务的权限请求在任何会话界面都可弹窗（事件带 session_id 标注来源）
- 清理按来源收窄：任务收尾只清自己发出的请求，不误杀其他并发任务
"""

from __future__ import annotations

import asyncio
from uuid import uuid4


class PermissionBridge:
    """权限请求挂起器，在引擎和 HTTP 前端之间传递权限决策。"""

    def __init__(self) -> None:
        # request_id -> Future，存等待决策的挂起请求
        self._pending: dict[str, asyncio.Future[str]] = {}
        # request_id -> 请求事件元数据（未决表，状态查询式）
        self._pending_meta: dict[str, dict] = {}

    async def request_permission(
        self,
        tool_name: str,
        tool_input: dict,
        reason: str,
        session_id: str = "",
    ) -> str:
        """引擎调用的权限回调，挂起等待前端决策。

        引擎在工具调用前调这个方法，方法内部创建 Future 挂起，
        把请求信息登记进未决表，等前端 POST /api/permission 回传决策后解除挂起。

        Args:
            tool_name: 工具名称
            tool_input: 工具输入参数
            reason: 请求权限的原因
            session_id: 来源会话 id（后台任务标注，跨会话弹窗用）

        Returns:
            决策字符串："allow" / "deny" / "always_allow"
        """
        request_id = str(uuid4())
        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()
        self._pending[request_id] = future
        self._pending_meta[request_id] = {
            "type": "permission_request",
            "request_id": request_id,
            "tool_name": tool_name,
            "tool_input": tool_input,
            "reason": reason,
            "session_id": session_id,
        }

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
        self._pending_meta.pop(request_id, None)
        if future is None:
            return False
        if not future.done():
            future.set_result(decision)
        return True

    def get_pending_requests(self) -> list[dict]:
        """返回全部未决权限请求（状态查询式，非消费）。

        多个 SSE 流并发轮询都看得到同一份；请求被 resolve 或按来源
        清理后从列表消失。前端按 request_id 去重展示。
        """
        return list(self._pending_meta.values())

    def clear_pending(self, session_id: str | None = None) -> None:
        """清理挂起请求与未决表。

        Args:
            session_id: 给定时只清该会话发出的请求（任务收尾按来源清理，
                不误杀其他并发任务正挂起的请求）；不给定时清全部
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
