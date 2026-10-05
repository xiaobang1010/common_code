"""对话路由：会话状态、SSE 流式对话、取消。"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import replace
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from query.engine import QueryEngine, build_engine_config
from query.loop import LoopResult
from query.services.api.llm import StreamEvent
from query.utils.messages import extract_text_from_content, sanitize_dangling_tool_calls
from server.paths import project_root
from server.routers.sessions.routes import get_git_branch
import server.state

# 图片附件限额（与前端压缩目标同口径，度量以 data URL 解码后字节为准）
_MAX_IMAGES_PER_MESSAGE = 4
_MAX_IMAGE_DECODED_BYTES = 5 * 1024 * 1024

logger = logging.getLogger(__name__)

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

    # 最近一回合退出信息：单列 SELECT（不反序列化 messages），供前端历史重建
    # 恢复真实退出原因；查看会话未装载（view_session 为 None）自然返回 {}
    session_store = server.state.session_store
    last_turn = session_store.get_session_last_turn(view_session) if session_store is not None else {}

    return {
        # 轮询高频回传：image_url 的 base64 在响应副本上替换为占位哨兵
        "messages": _omit_image_payloads(messages),
        "started_at": started_at,
        "last_turn": last_turn,
        "model": state.model,
        "token_usage": {
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "cache_read_input_tokens": usage.cache_read_input_tokens,
            "cache_creation_input_tokens": usage.cache_creation_input_tokens,
            # 累计实际发送的输入 token 总量（缓存命中率分母）
            "total_input_tokens": usage.total_input_tokens,
            # 当前上下文大小（最近一次请求的 prompt_tokens，覆盖不累加）
            "last_prompt_tokens": usage.last_prompt_tokens,
            # 已缓存大小（最近一次请求的 cache_creation_input_tokens，覆盖不累加）
            "last_cache_creation": usage.last_cache_creation,
        },
        # 最近一次请求的上下文分类 token 估算（覆盖不累加），供「上下文容量」面板
        "context_breakdown": state.context_breakdown,
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
        # 上下文分类估算（event_type="context_breakdown"），前端面板实时刷新用
        if event.breakdown is not None:
            result["breakdown"] = event.breakdown
        # 压缩事件载荷（compact_started/completed/failed），前端分隔线/提示用
        if event.compact_info is not None:
            result["compact_info"] = event.compact_info
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


def _validate_images(images_raw: Any) -> tuple[list[dict], str | None]:
    """兜底校验附件：数量/大小/mime 一致性/模型能力，返回归一化列表或错误。

    度量口径与前端一致按解码后字节（base64 长度×3/4 折算）；
    以内嵌 media type 为准校验与 mime 字段一致（不一致按形态错误拒绝）。
    """
    if not isinstance(images_raw, list) or not images_raw:
        return [], "images 必须是非空列表"
    if len(images_raw) > _MAX_IMAGES_PER_MESSAGE:
        return [], f"单条消息最多携带 {_MAX_IMAGES_PER_MESSAGE} 张图片"

    import base64
    import os

    from query.services.api.client import get_default_model
    from startup.model.config import get_model_config

    model = os.environ.get("COMMON_CODE_MODEL") or get_default_model()
    if "image" not in get_model_config(model).input_types:
        return [], "当前模型不支持图片输入"

    normalized: list[dict] = []
    for i, item in enumerate(images_raw):
        if not isinstance(item, dict):
            return [], f"images[{i}] 必须是对象"
        data_url = item.get("data_url")
        mime = item.get("mime")
        name = item.get("name") if isinstance(item.get("name"), str) else ""
        if not isinstance(data_url, str) or not data_url.startswith("data:"):
            return [], f"images[{i}] 缺少完整 data URL"
        if not isinstance(mime, str) or not mime.startswith("image/"):
            return [], f"images[{i}] mime 必须是 image/* 类型"
        # 内嵌 media type 为准，与声明的 mime 必须一致
        embedded = data_url[5:data_url.find(";")] if ";" in data_url[5:] else ""
        if embedded != mime:
            return [], f"images[{i}] 声明的 mime 与 data URL 内嵌类型不一致"
        try:
            decoded = base64.b64decode(data_url.split(",", 1)[1], validate=False)
        except Exception:
            return [], f"images[{i}] base64 解码失败"
        if len(decoded) > _MAX_IMAGE_DECODED_BYTES:
            return [], f"images[{i}] 超过单张 5MB 上限"
        normalized.append({"name": name, "mime": mime, "data_url": data_url})
    return normalized, None


def _build_user_content(prompt: str, images: list[dict]):
    """有图时构造 OpenAI 风格 content parts（落库格式即 provider 直传格式）；
    无图保持纯字符串，行为与改造前一致。"""
    if not images:
        return prompt
    parts: list[dict] = []
    if prompt and prompt.strip():
        parts.append({"type": "text", "text": prompt})
    for img in images:
        parts.append({"type": "image_url", "image_url": {"url": img["data_url"]}})
    return parts


def _omit_image_payloads(messages: Any) -> Any:
    """/api/state 响应副本：image_url 的 base64 替换为 __omitted__ 哨兵。

    轮询高频回传，完整 data URL 会拖住渲染；前端遇哨兵复用本地缓存，
    历史接口（sessions get / switch）仍返回完整数据。不修改引擎原消息。
    """
    if not isinstance(messages, list):
        return messages
    out: list = []
    for msg in messages:
        content = msg.get("content") if isinstance(msg, dict) else None
        if isinstance(content, list) and any(
            isinstance(b, dict) and b.get("type") == "image_url" for b in content
        ):
            new_blocks = []
            for b in content:
                if isinstance(b, dict) and b.get("type") == "image_url":
                    url = (b.get("image_url") or {}).get("url", "")
                    mime = url[5:url.find(";")] if url.startswith("data:") and ";" in url else "image/png"
                    new_blocks.append(
                        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,__omitted__"}}
                    )
                else:
                    new_blocks.append(b)
            out.append({**msg, "content": new_blocks})
        else:
            out.append(msg)
    return out


def _resolve_reasoning_level(raw: Any) -> str:
    """按活动模型等级列表校验请求携带的推理等级（get_model_config 单源）。

    与 build_engine_config 的模型解析同口径（COMMON_CODE_MODEL 优先）；
    非法/缺失返回空串（跟随模型默认）并记录告警，会话不中断。
    """
    if not isinstance(raw, str) or not raw:
        return ""
    import os

    from query.services.api.client import get_default_model
    from startup.model.config import get_model_config

    model = os.environ.get("COMMON_CODE_MODEL") or get_default_model()
    if raw in get_model_config(model).reasoning_levels:
        return raw
    logger.warning("推理等级 %s 不在模型 %s 的等级列表内，按未选择处理", raw, model)
    return ""


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
        # 压缩摘要消息对模型可见、对用户隐藏，不计入可见序号（与前端判定一致）
        if msg.get("_compact_summary"):
            continue
        content = msg.get("content", "")
        # 三分支与前端 parseUserMessage + extractContentParts 同规则：
        # str 原样判定 / list 提取拼接 text 块后判定 / 其他形态维持跳过
        if isinstance(content, list):
            content = extract_text_from_content(content)
        if not isinstance(content, str):
            continue
        if content.startswith(_SYSTEM_REMINDER_PREFIX):
            continue
        indexes.append(i)
    return indexes


# ---------------------------------------------------------------------------
# _start_run - 会话运行任务启动核心（用户 SSE 路径与后台唤起路径共享）
# ---------------------------------------------------------------------------


def _start_run(
    session_id: str,
    prompt: str | list,
    *,
    edit_user_index: int | None = None,
    take_view_pointer: bool = True,
    reasoning_level: str = "",
    promoted_extra: list[dict] | None = None,
) -> tuple[server.state.RunContext | None, str | None]:
    """创建会话运行任务：串行守卫 → 前缀快照 → 持久化 → 引擎 → 后台任务。

    会话必须已存在（自动建会话是 SSE 入口的专属前置逻辑）；收尾统一走
    run_engine 的 finally（落库/last_turn/视图回写/桥清理/移出注册表），
    用户路径与唤起路径不再有两套收尾。

    Args:
        session_id: 目标聊天会话 id
        prompt: 本轮用户消息，文本或 content parts（含图时为 parts 列表；
            唤起路径为合并后的通知正文，恒为纯文本）
        edit_user_index: 编辑重发时按可见用户消息序号截断历史（仅用户路径）
        take_view_pointer: 是否把查看指针改写指向本会话。用户路径 True；
            唤起路径 False——后台唤起不得劫持用户正在查看的其他会话
            （/api/state 的实时消息来源与 /api/abort 缺省目标都跟随该指针）

    Returns:
        (run, error)：error 非 None 时 run 为 None，error 为面向用户的文案
    """
    from query.services.pricing import calculate_cost

    app_state = server.state.app_state
    permission_bridge = server.state.permission_bridge
    question_bridge = server.state.question_bridge
    session_store = server.state.session_store
    run_session_id = session_id

    # ---- 同会话串行约束：同一会话同时只允许一个运行任务 ----
    if run_session_id in server.state.running_runs:
        return None, '当前会话已有任务在运行，请先停止或等待完成'

    session = session_store.get_session(run_session_id) if session_store is not None else None
    if session is None:
        return None, '会话不存在'

    # ---- 快照：DB 会话消息前缀（不含本条 user，user 由 submitMessage 内部追加） ----
    # 前缀过一遍悬空 tool_calls 清洗：防御存量脏数据进入新一轮请求（不回写 DB）
    prefix_messages: list[dict] = sanitize_dangling_tool_calls(list(session.messages))

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
            return None, '编辑位置无效，历史未被修改'
        prefix_messages = prefix_messages[: visible[edit_user_index]]

    # ---- 起轮转正：上一轮运行期入队、尚未等到轮次边界的 queued 行
    # （用户消息与通知）在本条消息之前全量转正，防新消息插队旧队列；
    # 唤起路径已自行取走的非文本消息经 promoted_extra 补位 ----
    try:
        from tools.subagent.notify import promote_queued_messages

        prefix_messages = [*prefix_messages, *promote_queued_messages(run_session_id)]
    except Exception:
        logging.getLogger(__name__).warning("起轮转正队列失败，按无队列继续", exc_info=True)
    if promoted_extra:
        prefix_messages = [*prefix_messages, *promoted_extra]

    # 任务工作区：会话所属工作区（跨工作区后台任务的 cwd 隔离依据）
    task_workspace = session.workspace_path or project_root()

    # ---- 用户消息立即持久化（前缀 + 本条），标题即时生成 ----
    # 标题与空文本判定以提取后的文本为准：纯图片 parts（无 text 块）回退固定文案
    prompt_text = extract_text_from_content(prompt)
    if session_store is not None:
        try:
            session_store.save_messages(
                run_session_id, [*prefix_messages, {"role": "user", "content": prompt, "_ts": time.time() * 1000}]
            )
            if not session.title:
                if prompt_text.strip():
                    session_store.update_session_title(run_session_id, _extract_session_title(prompt_text))
                elif isinstance(prompt, list):
                    session_store.update_session_title(run_session_id, "图片消息")
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
        reasoning_level=reasoning_level,
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
    # 查看指针按来源参数化：用户路径注册时指向本会话；唤起路径保持不变
    if take_view_pointer:
        server.state.engine_session_id = run_session_id
    view_session_at_start = server.state.engine_session_id

    # ---- 任务事件分发：无订阅者时丢弃（不做无界缓冲） ----
    def dispatch(ev: Any) -> None:
        for queue in list(run.subscribers):
            queue.put_nowait(ev)

    # 回合退出信息捕获袋（run_engine 内按 dict 引用写入，收尾落库 last_turn；
    # 用可变容器避免嵌套函数 nonlocal 声明）
    turn_capture: dict = {"loop_result": None, "crashed": None}

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
                        # 命中率分母用协议正确的总输入；缺字段时回退 prompt_tokens
                        state.token_usage.total_input_tokens += ev.usage.get(
                            "total_input_tokens", prompt_tokens
                        )
                        state.token_usage.last_prompt_tokens = prompt_tokens
                        state.token_usage.last_cache_creation = cache_creation
                        cost = calculate_cost(state.model or "", ev.usage)
                        state.total_cost_usd += cost
                    # 拦截上下文分类估算事件，写入 AppState（覆盖不累加，
                    # 与 last_prompt_tokens 同口径：反映最近一次请求的上下文构成）
                    elif isinstance(ev, StreamEvent) and ev.type == "context_breakdown" and ev.breakdown:
                        app_state.get_state().context_breakdown = ev.breakdown
                    # 拦截循环退出结果：真实退出原因供收尾落库 last_turn（事件照常转发）
                    elif isinstance(ev, LoopResult):
                        turn_capture["loop_result"] = {
                            "reason": ev.reason,
                            "error": str(ev.error) if ev.error is not None else None,
                        }
                    dispatch(ev)
            finally:
                server.state.workspace_var.reset(token)
                server.state.session_var.reset(session_token)
        except Exception as e:
            turn_capture["crashed"] = str(e)
            dispatch(e)
        finally:
            # ---- 收尾统一清理路径：保存 -> 落退出原因 -> 回写视图 -> 移出注册表 -> 按来源清桥 -> 置位 ----
            try:
                # 入库前清洗：中断/输出超限恢复留下的悬空 tool_calls 就地补合成结果，
                # 保证 DB 里的历史序列始终合法；清洗后的列表同时用于提取 last_turn
                # 的 user_ts（与前端重建块的 startTime 同源，才能精确相等比对）
                sanitized_messages = sanitize_dangling_tool_calls(task_engine.mutable_messages)
                session_store.save_messages(run_session_id, sanitized_messages)
                # 压缩逃生门：全量转录与会话库同根落盘（覆盖式），续写消息引用该路径
                try:
                    session_store.export_transcript(run_session_id, sanitized_messages)
                except Exception:
                    logging.getLogger(__name__).warning(
                        "会话 %s 转录导出失败（压缩逃生门暂不可用，不影响对话）",
                        run_session_id, exc_info=True,
                    )
                final_session = session_store.get_session(run_session_id)
                if final_session and not final_session.title:
                    for msg in task_engine.mutable_messages:
                        if msg.get("role") == "user":
                            content = msg.get("content", "")
                            if isinstance(content, str) and content.strip():
                                session_store.update_session_title(run_session_id, _extract_session_title(content))
                                break
                # ---- 回合退出原因落库：前端历史重建据此恢复真实退出原因，
                # 不再把异常回合误标为「用户主动停止」。与 save_messages 同 try：
                # 消息都没存上时重建本身失真，last_turn 失去意义，一并跳过 ----
                turn_meta: dict = {"finished_at": time.time() * 1000}
                # user_ts 归属确认：取落库列表最后一条可见 user 消息的 _ts，
                # 且必须不早于本回合启动时刻（引擎在本回合内追加，天然晚于
                # started_at）；hook 拦截使本回合 user 未进列表时，取到的是
                # 上一回合消息 → 校验不过 → 不写 user_ts 键，前端按不可用处理，
                # 防止把本回合退出原因错套到上一回合的块上
                visible = _visible_user_indexes(sanitized_messages)
                if visible:
                    last_user = sanitized_messages[visible[-1]]
                    ts = last_user.get("_ts")
                    if isinstance(ts, (int, float)) and ts >= run.started_at * 1000:
                        turn_meta["user_ts"] = ts
                captured = turn_capture["loop_result"]
                if captured is not None:
                    turn_meta["reason"] = captured["reason"]
                    if captured["error"]:
                        turn_meta["error"] = captured["error"][:500]
                elif run_abort_event.is_set():
                    # 用户点停止：/api/abort 先置位事件再 cancel，含 cancel 强杀形态
                    turn_meta["reason"] = "aborted"
                elif turn_capture["crashed"] is not None:
                    turn_meta["reason"] = "error"
                    turn_meta["error"] = str(turn_capture["crashed"])[:500]
                else:
                    # 不置位事件的强杀（如删除会话走 stop_session_run）等无结果形态
                    turn_meta["reason"] = "error"
                    turn_meta["error"] = "回合未产出结果即结束"
                session_store.set_session_last_turn(run_session_id, turn_meta)
            except Exception:
                # 落库失败不再静默：上下文可能回退旧快照，必须留痕可查
                logging.getLogger(__name__).warning(
                    "会话 %s 收尾落库失败，下一轮可能基于旧历史重建上下文",
                    run_session_id, exc_info=True,
                )
            # 回写查看视图：查看会话未被切换（含切走又切回）时同步视图，
            # 否则用户看到进展回退、下一轮快照会用旧视图覆盖任务产出
            if server.state.engine_session_id == view_session_at_start:
                server.state.engine.mutable_messages = list(task_engine.mutable_messages)
            server.state.running_runs.pop(run_session_id, None)
            # 收尾补偿：父会话运行收尾窗口内到达的通知没有活跃通道负责
            #（loop 只在有工具调用的轮次边界 drain，最后一轮输出期间入队的
            # 通知会滞留），此刻已移出注册表，补一次唤起判定（守卫与去重
            # 均在唤起回调内）
            try:
                from tools.subagent.notify import pending_count as _notify_pending

                if _notify_pending(run_session_id) > 0:
                    _wake_parent(run_session_id)
            except Exception:
                pass
            if permission_bridge is not None:
                permission_bridge.clear_pending(session_id=run_session_id)
            if question_bridge is not None:
                question_bridge.clear_pending(session_id=run_session_id)
            run.finished.set()
            # 哨兵最后发：订阅者收到 None 时收尾已全部完成
            dispatch(None)

    run.task = asyncio.create_task(run_engine())
    return run, None


# ---------------------------------------------------------------------------
# 后台唤起（auto-resume）：子代理通知唤起空闲的父会话
# ---------------------------------------------------------------------------

# 正在唤起中的父会话去重集：通知风暴（多个子代理同时完成）只建一轮，
# 建任务期间新到的通知由该轮的活跃通道 drain
_waking_sessions: set[str] = set()


def _auto_resume_enabled() -> bool:
    """读取 subagents.auto_resume_parent 开关；配置读取失败按开启处理。"""
    from startup.config import get_global_config

    try:
        return get_global_config().subagents.auto_resume_parent
    except Exception:
        return True


def _wake_parent(session_id: str) -> None:
    """唤起回调（notify 钩子与收尾补偿共用）：空闲父会话建唤起轮次。

    守卫顺序：父会话在运行中 → 返回（活跃通道负责）；开关关闭 → 返回；
    已在去重集 → 返回；会话不存在（已删除）→ 丢弃通知。全部通过后
    创建后台唤起任务。
    """
    if session_id in server.state.running_runs:
        return
    if not _auto_resume_enabled():
        return
    if session_id in _waking_sessions:
        return
    session_store = server.state.session_store
    if session_store is not None and session_store.get_session(session_id) is None:
        return
    _waking_sessions.add(session_id)
    try:
        asyncio.create_task(_wake_run(session_id))
    except Exception:
        # 无事件循环等边缘环境：放弃本次唤起，通知留队列走既有语义
        _waking_sessions.discard(session_id)


async def _wake_run(session_id: str) -> None:
    """唤起任务体：取走全部队列通知合并为一条用户消息，建唤起轮次。

    通知取走即负责：启动失败静默降级不回灌（回灌会在持续失败时形成
    死循环）；take_view_pointer=False，唤起不劫持用户正在查看的会话。
    """
    try:
        from tools.subagent.notify import drain_notifications

        notices = drain_notifications(session_id)
        if not notices:
            return
        # 文本通知合并为一条唤起消息（现状语义）；非文本行（如入队的图片
        # parts 消息）保持原 dict 经 promoted_extra 补进前缀，不经合并丢内容
        texts = [
            n.get("content", "")
            for n in notices
            if isinstance(n.get("content"), str) and n.get("content")
        ]
        extras = [
            n for n in notices
            if not (isinstance(n.get("content"), str) and n.get("content"))
        ]
        merged = "\n\n".join(texts)
        if not merged and extras:
            prompt_content = extras[0].get("content", "")
            extras = extras[1:]
        else:
            prompt_content = merged
        run, error = _start_run(
            session_id, prompt_content,
            promoted_extra=extras or None, take_view_pointer=False,
        )
        if error is not None:
            logging.getLogger(__name__).warning("唤起会话 %s 未启动: %s", session_id, error)
    finally:
        _waking_sessions.discard(session_id)


def setup_wakeup_hook() -> None:
    """server 启动时调用：把唤起回调注册进通知队列（tools 层依赖倒置）。"""
    from tools.subagent.notify import register_wakeup_hook

    register_wakeup_hook(_wake_parent)


async def chat_event_stream(
    prompt: str | list,
    session_id: str = "",
    edit_user_index: int | None = None,
    reasoning_level: str = "",
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

    任务启动核心在 _start_run（与后台唤起路径共享），本生成器只负责
    自动建会话的前置逻辑与事件订阅转发。
    """
    session_store = server.state.session_store
    permission_bridge = server.state.permission_bridge
    question_bridge = server.state.question_bridge

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

    # ---- 任务启动核心（与后台唤起路径共享） ----
    run, error = _start_run(
        run_session_id, prompt,
        edit_user_index=edit_user_index, reasoning_level=reasoning_level,
    )
    if error is not None:
        yield f"data: {json.dumps({'type': 'error', 'error': error}, ensure_ascii=False)}\n\n"
        return

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
async def chat(body: dict):
    """SSE 流式对话接口。

    请求体：{"prompt": "...", "session_id": "...", "edit_user_index": 0,
             "reasoning_level": "low", "images": [{name, mime, data_url}]}
    edit_user_index 可选，编辑重发时传目标可见用户消息序号（0 起）；
    images 可选（仅首发携带），附件兜底校验在流开始前完成，
    失败返回 JSONResponse(400, {"ok": false, "error": 文案})；
    返回：text/event-stream，每行 data: {JSON}\n\n
    """
    prompt = body.get("prompt", "")
    if not isinstance(prompt, str):
        return JSONResponse(status_code=400, content={"ok": False, "error": "prompt 必须是字符串"})
    session_id = body.get("session_id", "")
    edit_user_index = body.get("edit_user_index")
    # 可选推理等级：非法值忽略并告警（跟随模型默认），不中断会话
    reasoning_level = _resolve_reasoning_level(body.get("reasoning_level"))
    # 附件兜底校验（数量/大小/mime 一致性/模型能力），有图时构造 parts 落库
    images_raw = body.get("images")
    if images_raw:
        images, err = _validate_images(images_raw)
        if err:
            return JSONResponse(status_code=400, content={"ok": False, "error": err})
        prompt = _build_user_content(prompt, images)
    # ---- 运行中入队（对齐统一输入队列）：不再拒绝，写 session_input 排队，
    # 轮次边界转正注入；编辑重发有截断语义，维持守卫拒绝 ----
    if session_id and edit_user_index is None and session_id in server.state.running_runs:
        store = server.state.session_store
        if store is not None:
            queue_id = store.admit_session_input(
                session_id, "sendText", "queue", {"role": "user", "content": prompt}
            )
            return JSONResponse(
                content={"ok": True, "queued": True, "queue_id": queue_id, "session_id": session_id}
            )
    return StreamingResponse(
        chat_event_stream(prompt, session_id, edit_user_index, reasoning_level),
        media_type="text/event-stream",
    )


# ---------------------------------------------------------------------------
# GET /api/runs - 当前运行任务的会话键集
# ---------------------------------------------------------------------------


@router.get("/api/runs")
def list_runs() -> dict:
    """返回当前有运行任务的会话 id 集合（前端唤起感知轮询用，极轻）。

    与 /api/debug/tasks 不同，这里只有键集、无栈帧：外部唤起的运行任务
    需要被前端以低频轮询发现（5s），开销必须可忽略。
    """
    return {"running_session_ids": list(server.state.running_runs.keys())}


# ---------------------------------------------------------------------------
# 输入队列观测与撤销（运行中入队消息的前端管理入口）
# ---------------------------------------------------------------------------


@router.get("/api/session_inputs")
def list_session_inputs(session_id: str = "") -> dict:
    """列该会话未转正队列项（图片 base64 以占位符回传，前端队列条用）。"""
    store = server.state.session_store
    if store is None or not session_id:
        return {"items": []}
    return {"items": store.list_queued_inputs(session_id)}


@router.delete("/api/session_inputs/{row_id}")
def cancel_session_input(row_id: str) -> dict:
    """撤销队列项：仅未转正行生效（status=cancelled + user_removed）。"""
    store = server.state.session_store
    if store is None:
        return {"ok": False}
    return {"ok": store.cancel_session_input(row_id)}


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
