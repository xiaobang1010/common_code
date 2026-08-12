"""会话管理路由：创建、列表、详情、切换、删除。"""

from __future__ import annotations

import os
import subprocess

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from server.paths import project_root, set_project_root
import server.state

router = APIRouter()


# ---------------------------------------------------------------------------
# 会话管理 API
# ---------------------------------------------------------------------------


@router.post("/api/sessions")
async def create_session(body: dict) -> dict:
    """创建会话。

    请求体：{"workspace_path": "...", "title": "可选"}
    返回：{"session_id": "...", "workspace_path": "...", "title": "..."}
    """
    workspace_path = body.get("workspace_path", "")
    title = body.get("title", "")

    if not workspace_path:
        return {"ok": False, "error": "workspace_path is required"}

    # 创建会话时记录当前 git 分支
    branch = ""
    try:
        proc = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=workspace_path,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if proc.returncode == 0:
            branch = proc.stdout.strip()
    except (subprocess.SubprocessError, OSError):
        pass

    session = server.state.session_store.create_session(workspace_path, title=title, branch=branch)
    return {
        "session_id": session.id,
        "workspace_path": session.workspace_path,
        "title": session.title,
    }


@router.get("/api/sessions")
async def list_sessions(workspace_path: str = "") -> dict:
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
async def list_sessions_grouped() -> dict:
    """按工作区分组返回所有会话。

    返回 {"groups": [{"workspace": {path, name, last_used_at},
                       "sessions": [{id, title, workspace_path, branch,
                                     created_at, updated_at, message_count}]}]}
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
                    }
                    for s in sessions
                ],
            })
        # 透出当前运行任务（实时读取不落库，供列表标记"正在运行"）
        current_task = None
        if (
            server.state.current_task is not None
            and not server.state.current_task.done()
        ):
            current_task = {
                "session_id": server.state.current_session_id,
                "state": "running",
            }
        return {"groups": groups, "current_task": current_task}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.get("/api/sessions/{session_id}")
async def get_session(session_id: str) -> dict:
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
    """删除会话。"""
    server.state.session_store.delete_session(session_id)
    return {"ok": True}


@router.patch("/api/sessions/{session_id}")
async def update_session(session_id: str, body: dict) -> dict:
    """更新会话：支持 title（重命名）与 pinned（置顶）。"""
    if "title" in body:
        server.state.session_store.update_session_title(session_id, str(body["title"]))
    if "pinned" in body:
        server.state.session_store.update_session_pinned(session_id, bool(body["pinned"]))
    return {"ok": True}


@router.post("/api/sessions/{session_id}/switch")
async def switch_session(session_id: str) -> dict:
    """切换会话：加载消息到引擎，必要时切换工作区。

    如果会话的 workspace_path 与当前工作区不同，先切换工作区（更新 project_root + 重建引擎）。
    返回 {"ok": true, "messages": [...], "workspace_path": "..."}
    """
    session = server.state.session_store.get_session(session_id)
    if session is None:
        return JSONResponse(status_code=404, content={"error": "session not found"})

    # 如果工作区不同，切换工作区
    if session.workspace_path != project_root():
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

    # 加载消息到引擎
    server.state.engine.mutable_messages = list(session.messages)
    server.state.session_store.update_workspace_last_used(session.workspace_path)

    return {
        "ok": True,
        "messages": session.messages,
        "workspace_path": session.workspace_path,
    }
