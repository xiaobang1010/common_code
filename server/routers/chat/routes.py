"""对话路由：会话状态、SSE 流式对话、取消。"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse, StreamingResponse

from query.loop import LoopResult
from query.services.api.llm import StreamEvent
import server.state

router = APIRouter()


# ---------------------------------------------------------------------------
# GET /api/state - 获取会话状态
# ---------------------------------------------------------------------------


@router.get("/api/state")
async def get_state() -> dict:
    """返回会话状态：消息历史、模型、token 用量、成本、权限模式。"""
    from startup.bootstrap.state import get_permission_mode

    app_state = server.state.app_state
    engine = server.state.engine
    state = app_state.get_state()
    usage = state.token_usage
    return {
        "messages": engine.mutable_messages,
        "model": state.model,
        "token_usage": {
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "cache_read_input_tokens": usage.cache_read_input_tokens,
            "cache_creation_input_tokens": usage.cache_creation_input_tokens,
            # 当前上下文大小（最近一次请求的 prompt_tokens，覆盖不累加）
            "last_prompt_tokens": usage.last_prompt_tokens,
            # 已缓存大小（最近一次请求的 cache_creation_input_tokens，覆盖不累加）
            "last_cache_creation": usage.last_cache_creation,
        },
        "total_cost_usd": state.total_cost_usd,
        "permission_mode": get_permission_mode(),
    }


# ---------------------------------------------------------------------------
# 事件序列化
# ---------------------------------------------------------------------------


def serialize_event(event: Any) -> dict:
    """把引擎事件序列化为 JSON 字典。

    引擎 yield 三种事件：
      - StreamEvent: 流式事件（content/usage/error/done/tool_call_delta）
      - dict: OpenAI 格式消息（assistant/tool/compact boundary）
      - LoopResult: 循环退出结果

    只放非 None 的字段，避免前端收到一堆 null。
    """
    if isinstance(event, StreamEvent):
        result: dict = {"type": "stream", "event_type": event.type}
        if event.content is not None:
            result["content"] = event.content
        if event.usage is not None:
            result["usage"] = event.usage
        if event.error is not None:
            result["error"] = str(event.error)
        if event.finish_reason is not None:
            result["finish_reason"] = event.finish_reason
        # 工具调用增量字段，让前端能实时展示"正在调用工具 X"
        if event.tool_call_id is not None:
            result["tool_call_id"] = event.tool_call_id
        if event.tool_call_name is not None:
            result["tool_call_name"] = event.tool_call_name
        if event.tool_call_arguments is not None:
            result["tool_call_arguments"] = event.tool_call_arguments
        return result

    if isinstance(event, dict):
        return {"type": "message", "message": event}

    if isinstance(event, LoopResult):
        result = {"type": "loop_result", "reason": event.reason}
        if event.error is not None:
            result["error"] = str(event.error)
        return result

    # 未知事件类型，兜底处理
    return {"type": "unknown", "data": str(event)}


# ---------------------------------------------------------------------------
# POST /api/chat - SSE 流式对话
# ---------------------------------------------------------------------------


async def chat_event_stream(prompt: str, session_id: str = ""):
    """SSE 事件生成器。

    引擎的 submitMessage 是 async generator，当它挂起在 permission_prompt 回调时
    （await future 等待前端决策），生成器需要能继续推 permission_request 事件；
    同理，引擎挂起在 AskUserQuestion 提问回调时要推 question_request 事件。

    实现方式：用后台任务消费引擎事件放入队列，SSE 生成器从队列出队。
    队列空时（引擎可能挂起在权限/提问回调），轮询权限桥和问题桥推对应事件。
    """
    from query.services.pricing import calculate_cost

    app_state = server.state.app_state
    engine = server.state.engine
    permission_bridge = server.state.permission_bridge
    question_bridge = server.state.question_bridge
    session_store = server.state.session_store

    queue: asyncio.Queue = asyncio.Queue()

    async def consume_engine():
        """后台任务：消费引擎事件入队，同时累加 token 和成本到 AppState。"""
        try:
            async for ev in engine.submitMessage(
                prompt, user_context={}, system_context={}
            ):
                # 拦截 usage 事件，累加 token 和成本到 AppState（和 repl.py 逻辑一致）
                if isinstance(ev, StreamEvent) and ev.type == "usage" and ev.usage:
                    state = app_state.get_state()
                    prompt_tokens = ev.usage.get("prompt_tokens", 0)
                    completion_tokens = ev.usage.get("completion_tokens", 0)
                    cache_read = ev.usage.get("cache_read_input_tokens", 0)
                    cache_creation = ev.usage.get("cache_creation_input_tokens", 0)
                    state.token_usage.input_tokens += prompt_tokens
                    state.token_usage.output_tokens += completion_tokens
                    state.token_usage.cache_read_input_tokens += cache_read
                    state.token_usage.cache_creation_input_tokens += cache_creation
                    # 最近一次请求的上下文大小和缓存大小（覆盖，不累加）
                    state.token_usage.last_prompt_tokens = prompt_tokens
                    state.token_usage.last_cache_creation = cache_creation
                    cost = calculate_cost(state.model or "", ev.usage)
                    state.total_cost_usd += cost
                await queue.put(ev)
        except Exception as e:
            await queue.put(e)
        finally:
            # 哨兵，表示引擎结束
            await queue.put(None)

    task = asyncio.create_task(consume_engine())
    # 记录到全局变量，供 /api/abort 取消；同时记录所属会话，供列表 API 透出运行态
    server.state.current_task = task
    server.state.current_session_id = session_id

    try:
        while True:
            try:
                # 短超时轮询，让生成器有机会在引擎挂起时推权限请求
                ev = await asyncio.wait_for(queue.get(), timeout=0.2)
            except asyncio.TimeoutError:
                # 队列空，检查有没有待推送的权限请求或提问请求
                req = permission_bridge.get_pending_permission_request()
                if req is not None:
                    yield f"data: {json.dumps(req, ensure_ascii=False, default=str)}\n\n"
                else:
                    q = question_bridge.get_pending_question() if question_bridge else None
                    if q is not None:
                        yield f"data: {json.dumps(q, ensure_ascii=False, default=str)}\n\n"
                    else:
                        # 推心跳，让前端知道后端还活着（AI 可能在思考或执行工具）
                        yield f"data: {json.dumps({'type': 'heartbeat'})}\n\n"
                continue

            if ev is None:
                # 引擎结束，退出循环
                break

            if isinstance(ev, Exception):
                # 引擎抛异常，推错误事件
                yield f"data: {json.dumps({'type': 'error', 'error': str(ev)}, ensure_ascii=False)}\n\n"
                break

            yield f"data: {json.dumps(serialize_event(ev), ensure_ascii=False, default=str)}\n\n"

            # 每个事件后也检查权限请求和提问请求
            req = permission_bridge.get_pending_permission_request()
            if req is not None:
                yield f"data: {json.dumps(req, ensure_ascii=False, default=str)}\n\n"
            q = question_bridge.get_pending_question() if question_bridge else None
            if q is not None:
                yield f"data: {json.dumps(q, ensure_ascii=False, default=str)}\n\n"
    finally:
        # 客户端断开或引擎结束，清理后台任务
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        # 清理全局引用
        server.state.current_task = None
        server.state.current_session_id = None
        # 会话持久化：把完整消息列表存到 SQLite
        if session_id and session_store is not None:
            try:
                session_store.save_messages(session_id, engine.mutable_messages)
                # 自动生成标题：标题为空且存在用户消息时，取首条用户消息前 40 字符
                session = session_store.get_session(session_id)
                if session and not session.title:
                    for msg in engine.mutable_messages:
                        if msg.get("role") == "user":
                            content = msg.get("content", "")
                            if isinstance(content, str) and content.strip():
                                title = content.strip()[:40]
                                session_store.update_session_title(session_id, title)
                                break
            except Exception:
                pass


@router.post("/api/chat")
async def chat(body: dict) -> StreamingResponse:
    """SSE 流式对话接口。

    请求体：{"prompt": "...", "session_id": "..."}
    返回：text/event-stream，每行 data: {JSON}\n\n
    """
    prompt = body.get("prompt", "")
    session_id = body.get("session_id", "")
    return StreamingResponse(
        chat_event_stream(prompt, session_id),
        media_type="text/event-stream",
    )


# ---------------------------------------------------------------------------
# POST /api/abort - 取消当前查询
# ---------------------------------------------------------------------------


@router.post("/api/abort")
async def abort_query() -> JSONResponse:
    """取消当前正在进行的对话任务。

    通过取消 consume_engine 后台任务来中断对话，
    SSE 流会因任务取消而结束，前端收到连接关闭后恢复输入状态。
    """
    import server.state

    if server.state.current_task is not None and not server.state.current_task.done():
        server.state.current_task.cancel()
        try:
            await server.state.current_task
        except asyncio.CancelledError:
            pass
        server.state.current_task = None
        server.state.current_session_id = None
        return JSONResponse(content={"ok": True})
    return JSONResponse(content={"ok": False, "error": "no running task"})
