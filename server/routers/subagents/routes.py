"""子代理任务管理路由：列表、详情、输出、过程查看、停止。

数据源为 tools/subagent/registry.py 的进程级 SubagentTaskRegistry（运行态），
历史终态从 SessionStore 子会话（agent_meta）重建；过程与磁盘记录复用
tools/subagent/transcript.py。
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter()


# ---------------------------------------------------------------------------
# 历史重建辅助
# ---------------------------------------------------------------------------


def _iso_to_epoch(iso: str) -> float:
    """ISO 时间串转 epoch 秒（与注册表时间戳口径对齐），坏值回退 0。"""
    try:
        return datetime.fromisoformat(iso).timestamp()
    except (ValueError, TypeError):
        return 0.0


def _history_entries(session_id: str, exclude: set[str]) -> list[dict]:
    """从子会话重建历史终态条目（注册表重启后丢失的部分）。

    Args:
        session_id: 按父会话过滤（空串不过滤）
        exclude: 已在注册表中的 agent_id 集合（注册表优先，避免重复）
    """
    try:
        import server.state

        store = server.state.session_store
    except Exception:
        return []
    if store is None:
        return []
    entries: list[dict] = []
    try:
        rows = store.list_terminal_subagent_sessions(limit=100)
    except Exception:
        return []
    for row in rows:
        meta = row.agent_meta or {}
        agent_id = meta.get("agent_id") or ""
        if not agent_id or agent_id in exclude:
            continue
        if session_id and row.parent_session_id != session_id:
            continue
        entries.append(
            {
                "agent_id": agent_id,
                "session_id": agent_id,
                "parent_session_id": row.parent_session_id,
                "child_session_id": row.id,
                "agent_type": meta.get("agent_type", ""),
                "description": row.title,
                "status": meta.get("status", "completed"),
                "mode": meta.get("mode", "background"),
                "promoted": bool(meta.get("promoted", False)),
                "created_at": _iso_to_epoch(row.created_at),
                "updated_at": _iso_to_epoch(row.updated_at),
                "output_file": meta.get("output_file"),
                "usage": meta.get("usage") or {},
                "error": meta.get("error"),
                "origin": "history",
            }
        )
    return entries


# ---------------------------------------------------------------------------
# GET /api/subagents - 列出子代理任务
# ---------------------------------------------------------------------------


@router.get("/api/subagents")
def list_subagents(session_id: str = "") -> dict:
    """列出子代理任务：运行态取注册表，历史终态从子会话重建。

    查询参数 session_id 可选：按父会话过滤（聊天会话 id）。
    条目含 child_session_id / promoted / origin（registry=内存运行态与
    终态，history=从会话存储重建的历史）。
    """
    from tools.subagent.registry import get_subagent_registry

    tasks = get_subagent_registry().list_tasks(
        session_id=session_id or None,
    )
    entries: list[dict] = []
    seen: set[str] = set()
    for t in tasks:
        item = t.to_dict()
        item["origin"] = "registry"
        entries.append(item)
        seen.add(t.agent_id)

    history = _history_entries(session_id, seen)
    if history:
        entries.extend(history)
    return {"subagents": entries}


# ---------------------------------------------------------------------------
# GET /api/subagents/{agent_id} - 任务详情
# ---------------------------------------------------------------------------


@router.get("/api/subagents/{agent_id}")
def get_subagent(agent_id: str) -> JSONResponse:
    """返回单个子代理任务的完整信息。

    含状态/usage/时间/output_file/child_session_id/promoted，
    以及预算护栏信息（max_turns/token_budget 与用量）。
    """
    from tools.subagent.registry import get_subagent_registry

    task = get_subagent_registry().get(agent_id)
    if task is None:
        return JSONResponse(
            status_code=404, content={"error": f"subagent not found: {agent_id}"}
        )
    result = task.to_dict()
    # 已完成的任务附结果预览（全量走 output 端点）
    if task.final_text:
        result["result_preview"] = task.final_text[:500]
    # 预算护栏：上限来自上下文，用量运行中为累计值（终态时定稿）
    if task.ctx is not None:
        result["budget"] = {
            "max_turns": task.ctx.max_turns,
            "token_budget": task.ctx.token_budget,
            "usage": dict(task.usage),
        }
    return result


# ---------------------------------------------------------------------------
# GET /api/subagents/{agent_id}/output - 结果/中间输出
# ---------------------------------------------------------------------------


@router.get("/api/subagents/{agent_id}/output")
def get_subagent_output(agent_id: str) -> JSONResponse:
    """返回子代理输出：已完成返回最终结果，运行中返回当前中间输出与活动信息。"""
    from tools.subagent.registry import get_subagent_registry
    from tools.subagent.transcript import get_agent_transcript, read_task_output

    task = get_subagent_registry().get(agent_id)
    if task is None:
        return JSONResponse(
            status_code=404, content={"error": f"subagent not found: {agent_id}"}
        )

    # 已完成：注册表里的最终文本（含截断提示，全量落盘在 output_file）
    if task.final_text is not None:
        return {
            "agent_id": agent_id,
            "status": task.status,
            "output": task.final_text,
            "output_file": task.output_file,
        }

    # 运行中：优先读增量输出文件（后台/提升代理每轮追加），
    # 无增量文件时退回 transcript 最后一条 assistant 消息
    incremental = read_task_output(agent_id)
    if incremental:
        return {
            "agent_id": agent_id,
            "status": task.status,
            "output": incremental,
            "source": "incremental",
        }

    # 兜底：取 transcript 最后一条 assistant 消息作中间输出，
    # 附带最近工具名与已完成工具调用数（前端实时反馈用）
    transcript = get_agent_transcript(agent_id) or []
    intermediate = ""
    last_tool: str | None = None
    tool_calls_done = 0
    for msg in transcript:
        if msg.get("role") == "tool":
            tool_calls_done += 1
        if msg.get("role") == "assistant":
            content = msg.get("content") or ""
            if content:
                intermediate = content
            tool_calls = msg.get("tool_calls") or []
            if tool_calls:
                last_tool = tool_calls[-1].get("function", {}).get("name") or None
    return {
        "agent_id": agent_id,
        "status": task.status,
        "output": intermediate or "(no output yet)",
        "last_tool": last_tool,
        "tool_calls_done": tool_calls_done,
    }


# ---------------------------------------------------------------------------
# GET /api/subagents/{agent_id}/transcript - 过程记录
# ---------------------------------------------------------------------------


@router.get("/api/subagents/{agent_id}/transcript")
def get_subagent_transcript(agent_id: str) -> JSONResponse:
    """返回子代理的完整过程记录（从磁盘 transcript 重建）。"""
    from tools.subagent.transcript import get_agent_transcript

    transcript = get_agent_transcript(agent_id)
    if transcript is None:
        return JSONResponse(
            status_code=404, content={"error": f"no transcript for agent: {agent_id}"}
        )
    return {"agent_id": agent_id, "messages": transcript}


# ---------------------------------------------------------------------------
# POST /api/subagents/{agent_id}/stop - 停止子代理
# ---------------------------------------------------------------------------


@router.post("/api/subagents/{agent_id}/stop")
async def stop_subagent(agent_id: str) -> JSONResponse:
    """单独停止一个子代理任务（不影响父会话与其他任务）。

    走生命周期引擎的统一停止入口：取消驱动任务秒级生效；
    无驱动句柄时兜底置位中断事件（轮次边界优雅退出）。
    """
    from tools.subagent.lifecycle import stop_subagent as lifecycle_stop

    outcome = lifecycle_stop(agent_id)
    if outcome == "not_found":
        return JSONResponse(
            status_code=404, content={"error": f"subagent not found: {agent_id}"}
        )
    if outcome == "already_finished":
        from tools.subagent.registry import get_subagent_registry

        task = get_subagent_registry().get(agent_id)
        return {
            "agent_id": agent_id,
            "ok": True,
            "status": task.status if task is not None else "unknown",
            "message": "already finished",
        }
    return {"agent_id": agent_id, "ok": True, "status": outcome}
