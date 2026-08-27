"""会话管理路由：创建、列表、详情、切换、删除。"""

from __future__ import annotations

import os
import subprocess

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from server.paths import project_root, set_project_root
import server.state

router = APIRouter()


async def stop_session_run(session_id: str, timeout: float | None = None) -> bool:
    """中止指定会话的运行任务并等待其收尾（保存）完成。

    删除会话/工作区前调用。超时后不移出注册表（收尾由任务自己的
    finally 完成），返回 False，调用方应放弃目标操作而不是硬删--
    安全优先，宁可不操作也不丢数据。

    Args:
        session_id: 目标会话 id
        timeout: 等待收尾的超时秒数，None 时用 server.state.stream_finalize_timeout

    Returns:
        True 表示任务已收尾（或本没有运行任务），False 表示等待超时
    """
    import asyncio

    run = server.state.running_runs.get(session_id)
    if run is None:
        return True
    if timeout is None:
        timeout = getattr(server.state, "stream_finalize_timeout", 10.0)
    if not run.task.done():
        run.task.cancel()
        try:
            await run.task
        except asyncio.CancelledError:
            pass
    try:
        await asyncio.wait_for(run.finished.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        return False
    return True


def get_git_branch(workspace_path: str) -> str:
    """获取工作区当前 git 分支名，非 git 仓库或失败时返回空串。

    手动建会话与自动建会话共用，保证行为一致。
    """
    try:
        proc = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=workspace_path,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if proc.returncode == 0:
            return proc.stdout.strip()
    except (subprocess.SubprocessError, OSError):
        pass
    return ""


# ---------------------------------------------------------------------------
# 会话管理 API
# ---------------------------------------------------------------------------


@router.post("/api/sessions")
def create_session(body: dict) -> dict:
    """创建会话。

    请求体：{"workspace_path": "...", "title": "可选"}
    返回：{"session_id": "...", "workspace_path": "...", "title": "..."}
    """
    workspace_path = body.get("workspace_path", "")
    title = body.get("title", "")

    if not workspace_path:
        return {"ok": False, "error": "workspace_path is required"}

    # 创建会话时记录当前 git 分支
    branch = get_git_branch(workspace_path)

    session = server.state.session_store.create_session(workspace_path, title=title, branch=branch)
    return {
        "session_id": session.id,
        "workspace_path": session.workspace_path,
        "title": session.title,
    }


@router.get("/api/sessions")
def list_sessions(workspace_path: str = "") -> dict:
    """列出指定工作区的会话。

    参数 workspace_path：工作区路径。
    返回 {"sessions": [{id, title, workspace_path, branch, created_at, updated_at, message_count}]}
    不返回 messages（太大）。
    """
    if not workspace_path:
        return {"sessions": []}
    sessions = server.state.session_store.list_sessions(workspace_path)
    return {
        "sessions": [
            {
                "id": s.id,
                "title": s.title,
                "workspace_path": s.workspace_path,
                "branch": s.branch,
                "created_at": s.created_at,
                "updated_at": s.updated_at,
                "message_count": s.message_count,
            }
            for s in sessions
        ]
    }


@router.get("/api/sessions/grouped")
def list_sessions_grouped() -> dict:
    """按工作区分组返回所有会话，附带自定义任务分组与每条会话的归属。

    返回 {"groups": [{"workspace": {path, name, last_used_at},
                       "sessions": [{id, title, workspace_path, branch,
                                     created_at, updated_at, message_count,
                                     pinned, group_id}]}],
           "task_groups": [{id, name, color, created_at}],
           "current_tasks": [...]}
    """
    try:
        grouped = server.state.session_store.list_all_sessions_grouped()
        groups = []
        for workspace, sessions in grouped:
            groups.append({
                "workspace": {
                    "path": workspace.path,
                    "name": workspace.name,
                    "last_used_at": workspace.last_used_at,
                    "pinned": workspace.pinned,
                    "alias": workspace.alias,
                },
                "sessions": [
                    {
                        "id": s.id,
                        "title": s.title,
                        "workspace_path": s.workspace_path,
                        "branch": s.branch,
                        "created_at": s.created_at,
                        "updated_at": s.updated_at,
                        "message_count": s.message_count,
                        "pinned": s.pinned,
                        "group_id": s.group_id,
                    }
                    for s in sessions
                ],
            })
        task_groups = server.state.session_store.list_task_groups()
        # 透出所有运行任务（实时读取注册表不落库，供列表标记"正在运行"）
        current_tasks = [
            {"session_id": session_id, "state": "running"}
            for session_id, run in server.state.running_runs.items()
            if not run.finished.is_set()
        ]
        return {
            "groups": groups,
            "task_groups": [
                {"id": g.id, "name": g.name, "color": g.color, "created_at": g.created_at}
                for g in task_groups
            ],
            "current_tasks": current_tasks,
        }
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.get("/api/sessions/{session_id}")
def get_session(session_id: str) -> dict:
    """获取单个会话详情（含完整 messages）。

    返回 {"session": {...}, "messages": [...]}。
    不存在返回 404。
    """
    session = server.state.session_store.get_session(session_id)
    if session is None:
        return JSONResponse(status_code=404, content={"error": "session not found"})
    return {
        "session": {
            "id": session.id,
            "title": session.title,
            "workspace_path": session.workspace_path,
            "branch": session.branch,
            "created_at": session.created_at,
            "updated_at": session.updated_at,
            "message_count": session.message_count,
        },
        "messages": session.messages,
    }


@router.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str) -> dict:
    """删除会话。

    若目标会话是当前运行任务的会话，先中止任务并等待收尾（保存）落库，
    再删除记录——否则任务内容会随记录删除而蒸发。
    删除的是当前会话时，重置引擎消息列表为空：会话记录已不存在，
    引擎残留的消息若不清理，会在下次自动建会话/发消息时被快照串入新会话。
    """
    # 删除运行中任务的会话：先中止 + 等待收尾，内容落库后再删
    if server.state.running_runs.get(session_id) is not None:
        if not await stop_session_run(session_id):
            return JSONResponse(
                status_code=409,
                content={"error": "上一任务收尾超时，请重试"},
            )
    server.state.session_store.delete_session(session_id)
    # 删除的是引擎当前装载的会话：重置引擎消息列表，防残留历史被
    # 下次发消息的快照串入新会话（删当前会话后前端会切换或建新会话）
    if server.state.engine_session_id == session_id:
        server.state.engine.mutable_messages = []
        server.state.engine_session_id = None
    return {"ok": True}


@router.patch("/api/sessions/{session_id}")
def update_session(session_id: str, body: dict) -> dict:
    """更新会话：支持 title（重命名）、pinned（置顶）与 group_id（归组/移出分组）。"""
    if "title" in body:
        server.state.session_store.update_session_title(session_id, str(body["title"]))
    if "pinned" in body:
        server.state.session_store.update_session_pinned(session_id, bool(body["pinned"]))
    if "group_id" in body:
        # 空串表示移出分组；不存在的分组 id 拒绝并保持数据不变
        updated = server.state.session_store.update_session_group(
            session_id, str(body["group_id"] or "")
        )
        if not updated:
            return JSONResponse(
                status_code=404,
                content={"ok": False, "error": "session or group not found"},
            )
    return {"ok": True}


@router.post("/api/sessions/{session_id}/switch")
def switch_session(session_id: str) -> dict:
    """切换会话：中止运行中任务，加载消息到引擎，必要时切换工作区。

    如果会话的 workspace_path 与当前工作区不同，先切换工作区（更新 project_root + 重建引擎）。
    返回 {"ok": true, "messages": [...], "workspace_path": "..."}
    """
    session = server.state.session_store.get_session(session_id)
    if session is None:
        return JSONResponse(status_code=404, content={"error": "session not found"})

    # 不中止运行中任务：任务后台继续跑并写回原会话（后台任务模型）。
    # 切换仅更换查看视图引擎，任务的独立引擎不受影响

    # 如果工作区不同，切换工作区（workspace_path 为空的脏数据不覆盖全局根）
    if session.workspace_path and session.workspace_path != project_root():
        set_project_root(session.workspace_path)
        # 重建引擎
        from dataclasses import replace

        from query.engine import QueryEngine, build_engine_config

        q_bridge = server.state.question_bridge
        config = build_engine_config(
            permission_prompt=server.state.permission_bridge.request_permission,
            question_prompt=q_bridge.ask_question if q_bridge else None,
        )
        config = replace(config, cwd=session.workspace_path)
        new_engine = QueryEngine(config)
        # 替换全局引擎
        server.state.engine = new_engine
        # 新引擎消息列表为空，当前没有装载任何会话
        server.state.engine_session_id = None

    # 加载消息到引擎
    server.state.engine.mutable_messages = list(session.messages)
    server.state.engine_session_id = session_id
    server.state.session_store.update_workspace_last_used(session.workspace_path)

    return {
        "ok": True,
        "messages": session.messages,
        "workspace_path": session.workspace_path,
    }
