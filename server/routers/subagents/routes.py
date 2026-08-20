"""子代理任务管理路由：列表、详情、输出、过程查看、停止。

数据源为 tools/subagent/registry.py 的进程级 SubagentTaskRegistry，
过程与磁盘记录复用 tools/subagent/transcript.py。
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter()


# ---------------------------------------------------------------------------
# GET /api/subagents - 列出子代理任务
# ---------------------------------------------------------------------------


@router.get("/api/subagents")
def list_subagents(session_id: str = "") -> dict:
    """列出子代理任务。

    查询参数 session_id 可选：按父会话过滤（聊天会话 id）。
    """
    from tools.subagent.registry import get_subagent_registry

    tasks = get_subagent_registry().list_tasks(
        session_id=session_id or None,
    )
    return {"subagents": [t.to_dict() for t in tasks]}


# ---------------------------------------------------------------------------
# GET /api/subagents/{agent_id} - 任务详情
# ---------------------------------------------------------------------------


@router.get("/api/subagents/{agent_id}")
def get_subagent(agent_id: str) -> JSONResponse:
    """返回单个子代理任务的完整信息（状态/usage/时间/output_file）。"""
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
    return result


# ---------------------------------------------------------------------------
# GET /api/subagents/{agent_id}/output - 结果/中间输出
# ---------------------------------------------------------------------------


@router.get("/api/subagents/{agent_id}/output")
def get_subagent_output(agent_id: str) -> JSONResponse:
    """返回子代理输出：已完成返回最终结果，运行中返回当前中间输出与活动信息。"""
    from tools.subagent.registry import get_subagent_registry
    from tools.subagent.transcript import get_agent_transcript

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

    # 运行中：取 transcript 最后一条 assistant 消息作中间输出，
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

    后台任务取消 asyncio 任务引用；前台任务置位其 abort 事件
    （在下个轮次边界优雅退出，父循环等待它结束）。
    """
    from tools.subagent.registry import (
        STATUS_STOPPED,
        TERMINAL_STATUSES,
        get_subagent_registry,
    )

    registry = get_subagent_registry()
    task = registry.get(agent_id)
    if task is None:
        return JSONResponse(
            status_code=404, content={"error": f"subagent not found: {agent_id}"}
        )

    if task.status in TERMINAL_STATUSES:
        return {"agent_id": agent_id, "ok": True, "status": task.status,
                "message": "already finished"}

    # 后台任务：cancel asyncio 引用（_run_background 记 stopped）
    if task.task is not None and not task.task.done():
        task.task.cancel()
        return {"agent_id": agent_id, "ok": True, "status": "stopping"}

    # 前台任务：置位其 abort 事件（runner 在轮次边界检测后优雅退出）
    if task.ctx is not None and task.ctx.abort_event is not None:
        task.ctx.abort_event.set()
        return {"agent_id": agent_id, "ok": True, "status": "stopping"}

    # 兜底：无从属句柄，直接标记 stopped
    registry.mark_status(agent_id, STATUS_STOPPED, error="stopped by request")
    return {"agent_id": agent_id, "ok": True, "status": "stopped"}
