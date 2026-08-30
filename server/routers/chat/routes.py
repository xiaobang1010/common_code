"""对话路由：会话状态、SSE 流式对话、取消。"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import replace
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from query.engine import QueryEngine, build_engine_config
from query.loop import LoopResult
from query.services.api.llm import StreamEvent
from query.utils.messages import sanitize_dangling_tool_calls
from server.paths import project_root
from server.routers.sessions.routes import get_git_branch
import server.state

router = APIRouter()


# ---------------------------------------------------------------------------
# GET /api/state - 获取会话状态
# ---------------------------------------------------------------------------


@router.get("/api/state")
async def get_state() -> dict:
    """返回会话状态：消息历史、模型、token 用量、成本、权限模式。

    当前查看会话有运行中的后台任务时，返回任务引擎的实时消息
    （切回会话能看到任务进展），否则返回全局查看视图引擎的消息。
    """
    from startup.bootstrap.state import get_permission_mode

    app_state = server.state.app_state
    state = app_state.get_state()
    usage = state.token_usage

    # 运行任务的实时消息优先
    messages: Any = server.state.engine.mutable_messages
    view_session = server.state.engine_session_id
    run = server.state.running_runs.get(view_session) if view_session else None
    started_at: float | None = None
    if run is not None and not run.finished.is_set():
        messages = run.engine.mutable_messages
        started_at = run.started_at

    return {
        "messages": messages,
        "started_at": started_at,
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

# 技能重写提示形状（/api/command 技能命中时前端发来的 prompt）：标题取任务描述
_SKILL_PROMPT_RE = re.compile(
    r"^Use the skill named `([^`\n]+)` for this turn\.\n[\s\S]*?\nUser request:[ \t]*([\s\S]*)$"
)


def _extract_session_title(prompt: str) -> str:
    """从 prompt 提取会话标题。

    技能重写提示取 User request 段（空段回退固定文案 /spec）；
    其余走原 prompt[:40] 截断逻辑。
    """
    m = _SKILL_PROMPT_RE.match(prompt)
    if m:
        task = m.group(2).strip()
        return task[:40] if task else "/spec"
    return prompt.strip()[:40]


# 系统注入消息前缀：与前端 parseUserMessage（frontend/src/utils/skillParse.ts）
# 的 startsWith 判定一致（不 strip），编辑重发的可见序号两侧必须同规则
_SYSTEM_REMINDER_PREFIX = "<system-reminder>"


def _visible_user_indexes(messages: list[dict]) -> list[int]:
    """返回可见用户消息在 messages 中的下标列表。

    可见判定与前端 parseUserMessage 对齐：`<system-reminder>` 开头为
    系统注入消息（skip），其余 user 消息可见——含技能重写提示
    （_SKILL_PROMPT_RE 命中形状）。编辑重发的 edit_user_index 即此
    列表的下标。
    """
    indexes: list[int] = []
    for i, msg in enumerate(messages):
        if not isinstance(msg, dict) or msg.get("role") != "user":
            continue
        content = msg.get("content", "")
        if not isinstance(content, str):
            continue
        if content.startswith(_SYSTEM_REMINDER_PREFIX):
            continue
        indexes.append(i)
    return indexes


async def chat_event_stream(
    prompt: str, session_id: str = "", edit_user_index: int | None = None
):
    """SSE 事件生成器（订阅者角色）。

    任务模型：每次对话创建独立 RunContext（专属 QueryEngine + 消息缓冲 +
    asyncio 任务），绑定启动时的会话。本生成器只是任务的订阅者：
    断开仅注销订阅，任务在后台继续运行；收尾时保存到绑定会话，
    若查看会话未被切换（engine_session_id == 启动值）则回写查看视图。

    保留语义（chat-session-binding）：session_id 为空自动建会话（校验工作区
    已登记）、启动前立即持久化「DB 前缀 + 本条 user」、标题即时生成、
    session_meta 固定回传、同会话串行约束。

    编辑重发：edit_user_index 非 None 时，按可见用户消息序号（_visible_user_indexes）
    定位 DB 中的目标消息，把该消息及其后全部截掉，新 prompt 作为该位置的用户
    消息重跑一轮；索引越界时 yield error 事件返回，不做任何持久化。
    """
    from query.services.pricing import calculate_cost

    app_state = server.state.app_state
    permission_bridge = server.state.permission_bridge
    question_bridge = server.state.question_bridge
    session_store = server.state.session_store

    # ---- 会话确定（自动建会话保留语义） ----
    run_session_id = session_id
    if not run_session_id:
        workspace_path = project_root()
        # 工作区已选择判定：当前路径已登记在工作区表
        registered = False
        if session_store is not None:
            registered = any(w.path == workspace_path for w in session_store.list_workspaces())
        if not registered:
            yield f"data: {json.dumps({'type': 'error', 'error': '请先选择工作区'})}\n\n"
            return
        session = session_store.create_session(workspace_path, title="", branch=get_git_branch(workspace_path))
        run_session_id = session.id

    # ---- 同会话串行约束：同一会话同时只允许一个运行任务 ----
    if run_session_id in server.state.running_runs:
        yield (
            f"data: {json.dumps({'type': 'error', 'error': '当前会话已有任务在运行，请先停止或等待完成'}, ensure_ascii=False)}\n\n"
        )
        return

    # ---- 快照：DB 会话消息前缀（不含本条 user，user 由 submitMessage 内部追加） ----
    # 前缀过一遍悬空 tool_calls 清洗：防御存量脏数据进入新一轮请求（不回写 DB）
    session = session_store.get_session(run_session_id) if session_store is not None else None
    prefix_messages: list[dict] = (
        sanitize_dangling_tool_calls(list(session.messages)) if session else []
    )

    # ---- 编辑重发：截断到目标可见用户消息之前（该消息由新 prompt 替换重跑） ----
    # 截断作用于清洗后的前缀，随下方 save_messages 一并持久化；越界在持久化前
    # 拒绝，历史不受影响。sanitize 只插入 tool 消息，不改 user 消息的相对次序，
    # 可见序号与前端按块推算的一致
    if edit_user_index is not None:
        visible = _visible_user_indexes(prefix_messages)
        if (
            isinstance(edit_user_index, bool)
            or not isinstance(edit_user_index, int)
            or not 0 <= edit_user_index < len(visible)
        ):
            yield (
                f"data: {json.dumps({'type': 'error', 'error': '编辑位置无效，历史未被修改'}, ensure_ascii=False)}\n\n"
            )
            return
        prefix_messages = prefix_messages[: visible[edit_user_index]]

    # 任务工作区：会话所属工作区（跨工作区后台任务的 cwd 隔离依据）
    task_workspace = session.workspace_path if session else project_root()

    # ---- 用户消息立即持久化（前缀 + 本条），标题即时生成 ----
    if session_store is not None:
        try:
            session_store.save_messages(
                run_session_id, [*prefix_messages, {"role": "user", "content": prompt, "_ts": time.time() * 1000}]
            )
            if session is not None and not session.title and prompt.strip():
                session_store.update_session_title(run_session_id, _extract_session_title(prompt))
        except Exception:
            pass

    # ---- 创建任务引擎与 RunContext ----
    async def task_permission_prompt(tool_name: str, tool_input: dict, reason: str) -> str:
        # 闭包携带来源会话，桥的请求事件据此标注（跨会话可见）
        return await permission_bridge.request_permission(
            tool_name, tool_input, reason, session_id=run_session_id
        )

    async def task_question_prompt(question: str, options: list[dict]) -> str:
        return await question_bridge.ask_question(question, options, session_id=run_session_id)

    # 任务级中断事件：/api/abort 置位后传导到引擎上下文与前台子代理
    run_abort_event = asyncio.Event()
    config = build_engine_config(
        permission_prompt=task_permission_prompt,
        question_prompt=task_question_prompt if question_bridge else None,
        abort_event=run_abort_event,
    )
    config = replace(config, cwd=task_workspace)
    # 引擎绑定聊天会话 id：子代理注册表按父会话关联、通知按会话投递
    task_engine = QueryEngine(config, initial_messages=prefix_messages, session_id=run_session_id)

    run = server.state.RunContext(
        session_id=run_session_id,
        engine=task_engine,
        started_at=time.time(),
        abort_event=run_abort_event,
    )
    server.state.running_runs[run_session_id] = run
    # 注册时把查看会话指向本会话并记录启动值（收尾回写判定用；
    # 自动建会话场景由 None 指向新会话；run 期间 switch 会改变它）
    server.state.engine_session_id = run_session_id
    view_session_at_start = run_session_id

    # ---- 任务事件分发：无订阅者时丢弃（不做无界缓冲） ----
    def dispatch(ev: Any) -> None:
        for queue in list(run.subscribers):
            queue.put_nowait(ev)

    async def run_engine() -> None:
        """后台任务体：跑引擎循环，收尾统一走清理路径。"""
        try:
            # cwd 隔离：任务上下文里设置自己的工作区，
            # 任务内的工具沙箱/Bash/记忆归属/提示词工作区信息都取它；
            # session_var 同步记录任务所属会话，供写盘事件钩子记 spec 归属
            token = server.state.workspace_var.set(task_workspace)
            session_token = server.state.session_var.set(run_session_id)
            try:
                # user_context 必须传 None：引擎以「user_context 为 None」判定首轮记忆注入
                async for ev in task_engine.submitMessage(prompt, user_context=None, system_context=None):
                    # 拦截 usage 事件，累加 token 和成本到 AppState
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
                        state.token_usage.last_prompt_tokens = prompt_tokens
                        state.token_usage.last_cache_creation = cache_creation
                        cost = calculate_cost(state.model or "", ev.usage)
                        state.total_cost_usd += cost
                    dispatch(ev)
            finally:
                server.state.workspace_var.reset(token)
                server.state.session_var.reset(session_token)
        except Exception as e:
            dispatch(e)
        finally:
            # ---- 收尾统一清理路径：保存 -> 回写视图 -> 移出注册表 -> 按来源清桥 -> 置位 ----
            try:
                # 入库前清洗：中断/输出超限恢复留下的悬空 tool_calls 就地补合成结果，
                # 保证 DB 里的历史序列始终合法
                session_store.save_messages(
                    run_session_id, sanitize_dangling_tool_calls(task_engine.mutable_messages)
                )
                final_session = session_store.get_session(run_session_id)
                if final_session and not final_session.title:
                    for msg in task_engine.mutable_messages:
                        if msg.get("role") == "user":
                            content = msg.get("content", "")
                            if isinstance(content, str) and content.strip():
                                session_store.update_session_title(run_session_id, _extract_session_title(content))
                                break
            except Exception:
                pass
            # 回写查看视图：查看会话未被切换（含切走又切回）时同步视图，
            # 否则用户看到进展回退、下一轮快照会用旧视图覆盖任务产出
            if server.state.engine_session_id == view_session_at_start:
                server.state.engine.mutable_messages = list(task_engine.mutable_messages)
            server.state.running_runs.pop(run_session_id, None)
            if permission_bridge is not None:
                permission_bridge.clear_pending(session_id=run_session_id)
            if question_bridge is not None:
                question_bridge.clear_pending(session_id=run_session_id)
            run.finished.set()
            # 哨兵最后发：订阅者收到 None 时收尾已全部完成
            dispatch(None)

    run.task = asyncio.create_task(run_engine())

    # ---- SSE 转发循环（订阅者；断开仅注销订阅，不取消任务） ----
    subscriber: asyncio.Queue = asyncio.Queue()
    run.subscribers.add(subscriber)

    def _format_pending() -> list[str]:
        """格式化当前所有未决权限/提问请求（桥为状态查询式，多个流都可见）。"""
        chunks: list[str] = []
        if permission_bridge is not None:
            for req in permission_bridge.get_pending_requests():
                chunks.append(f"data: {json.dumps(req, ensure_ascii=False, default=str)}\n\n")
        if question_bridge is not None:
            for q in question_bridge.get_pending_questions():
                chunks.append(f"data: {json.dumps(q, ensure_ascii=False, default=str)}\n\n")
        return chunks

    try:
        # session_meta 固定为首个事件
        meta_session = session_store.get_session(run_session_id) if session_store is not None else None
        yield (
            f"data: {json.dumps({'type': 'session_meta', 'session_id': run_session_id, 'title': meta_session.title if meta_session else ''}, ensure_ascii=False)}\n\n"
        )

        while True:
            try:
                ev = await asyncio.wait_for(subscriber.get(), timeout=0.2)
            except asyncio.TimeoutError:
                # 队列空：检查未决权限/提问请求，没有则推心跳保活
                pending = _format_pending()
                if pending:
                    for chunk in pending:
                        yield chunk
                else:
                    yield f"data: {json.dumps({'type': 'heartbeat'})}\n\n"
                continue

            if ev is None:
                # 任务结束，退出转发循环（后台任务自己完成收尾）
                break

            if isinstance(ev, Exception):
                yield f"data: {json.dumps({'type': 'error', 'error': str(ev)}, ensure_ascii=False)}\n\n"
                break

            yield f"data: {json.dumps(serialize_event(ev), ensure_ascii=False, default=str)}\n\n"

            # 每个事件后也检查权限/提问请求
            for chunk in _format_pending():
                yield chunk
    finally:
        # 断开仅注销订阅：任务在后台继续运行
        run.subscribers.discard(subscriber)


@router.post("/api/chat")
async def chat(body: dict) -> StreamingResponse:
    """SSE 流式对话接口。

    请求体：{"prompt": "...", "session_id": "...", "edit_user_index": 0}
    edit_user_index 可选，编辑重发时传目标可见用户消息序号（0 起）；
    返回：text/event-stream，每行 data: {JSON}\n\n
    """
    prompt = body.get("prompt", "")
    session_id = body.get("session_id", "")
    edit_user_index = body.get("edit_user_index")
    return StreamingResponse(
        chat_event_stream(prompt, session_id, edit_user_index),
        media_type="text/event-stream",
    )


# ---------------------------------------------------------------------------
# POST /api/abort - 取消当前查询
# ---------------------------------------------------------------------------


@router.post("/api/abort")
async def abort_query(request: Request) -> JSONResponse:
    """取消指定会话的运行任务。

    请求体 {"session_id": "..."} 可选，缺省作用于当前查看会话的任务。
    cancel 后等待该任务的 finished 收尾事件（保存完成）；超时不移出
    注册表、返回错误--收尾由任务自己的 finally 完成。
    """
    body: dict = {}
    try:
        body = await request.json()
    except Exception:
        pass
    session_id = body.get("session_id") or server.state.engine_session_id
    run = server.state.running_runs.get(session_id) if session_id else None
    if run is None:
        return JSONResponse(content={"ok": False, "error": "no running task"})
    # 先置位中断事件：前台子代理在轮次边界检测到后优雅退出并写 aborted 状态，
    # cancel 兜底强杀（模型调用阻塞中也能终止）
    run.abort_event.set()
    if not run.task.done():
        run.task.cancel()
        try:
            await run.task
        except asyncio.CancelledError:
            pass
    timeout = getattr(server.state, "stream_finalize_timeout", 10.0)
    try:
        await asyncio.wait_for(run.finished.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        return JSONResponse(content={"ok": False, "error": "stream finalize timeout"})
    return JSONResponse(content={"ok": True})


# ---------------------------------------------------------------------------
# GET /api/debug/tasks - 协程栈诊断（排查任务挂起）
# ---------------------------------------------------------------------------


@router.get("/api/debug/tasks")
async def debug_tasks() -> dict:
    """dump 所有 asyncio 任务栈帧与运行任务注册表状态。

    排查「任务长时间运行中但无产出」时，用它看任务协程挂在哪一行。
    """
    tasks_out: list[dict] = []
    for task in asyncio.all_tasks():
        if task is asyncio.current_task():
            continue
        frames = [
            f"{frame.f_code.co_filename}:{frame.f_lineno} {frame.f_code.co_name}"
            for frame in task.get_stack()
        ]
        tasks_out.append({
            "name": task.get_name(),
            "done": task.done(),
            "coro": repr(task.get_coro())[:150],
            "frames": frames,
        })
    runs_out: list[dict] = []
    for sid, run in server.state.running_runs.items():
        coro_frames: list[str] = []
        if run.task is not None and not run.task.done():
            coro_frames = [
                f"{frame.f_code.co_filename}:{frame.f_lineno} {frame.f_code.co_name}"
                for frame in run.task.get_stack()
            ]
        runs_out.append({
            "session_id": sid,
            "finished": run.finished.is_set(),
            "task_done": run.task.done() if run.task is not None else None,
            "frames": coro_frames,
            "subscribers": len(run.subscribers),
        })
    return {"tasks": tasks_out, "running_runs": runs_out}
