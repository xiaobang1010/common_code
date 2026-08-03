"""工作区管理路由：列表、添加、切换、删除。"""

from __future__ import annotations

import os
import subprocess

from fastapi import APIRouter

from server.paths import set_project_root
import server.state

router = APIRouter()


# ---------------------------------------------------------------------------
# 工作区管理 API
# ---------------------------------------------------------------------------


@router.get("/api/workspaces")
async def list_workspaces() -> dict:
    """列出所有工作区。

    返回 {"workspaces": [{path, name, last_used_at, session_count}]}
    """
    workspaces = server.state.session_store.list_workspaces()
    return {
        "workspaces": [
            {
                "path": w.path,
                "name": w.name,
                "last_used_at": w.last_used_at,
                "session_count": w.session_count,
            }
            for w in workspaces
        ]
    }


@router.post("/api/workspaces")
async def add_workspace(body: dict) -> dict:
    """添加工作区。

    请求体：{"path": "..."}
    返回 {"ok": true, "workspace": {...}}
    """
    path = body.get("path", "")
    if not path:
        return {"ok": False, "error": "path is required"}
    workspace = server.state.session_store.add_workspace(path)
    return {
        "ok": True,
        "workspace": {
            "path": workspace.path,
            "name": workspace.name,
            "last_used_at": workspace.last_used_at,
            "session_count": workspace.session_count,
        },
    }


@router.post("/api/workspaces/switch")
async def switch_workspace(body: dict) -> dict:
    """切换工作区。

    更新工作目录，重建 QueryEngine，更新最后使用时间，获取当前 git 分支。
    请求体：{"path": "..."}
    返回 {"ok": true, "workspace": {...}, "current_branch": "..."}
    """
    path = body.get("path", "")
    if not path:
        return {"ok": False, "error": "path is required"}

    from dataclasses import replace

    from query.engine import QueryEngine, build_engine_config

    # 1. 更新工作目录
    set_project_root(path)

    # 2. 重建引擎
    config = build_engine_config(permission_prompt=server.state.permission_bridge.request_permission)
    config = replace(config, cwd=path)
    new_engine = QueryEngine(config)
    # 替换全局引擎
    server.state.engine = new_engine

    # 3. 更新最后使用时间
    server.state.session_store.update_workspace_last_used(path)

    # 4. 获取当前 git 分支
    current_branch = ""
    try:
        proc = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=path,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if proc.returncode == 0:
            current_branch = proc.stdout.strip()
    except (subprocess.SubprocessError, OSError):
        pass

    # 获取工作区信息
    workspaces = server.state.session_store.list_workspaces()
    workspace_info = None
    for w in workspaces:
        if w.path == path:
            workspace_info = {
                "path": w.path,
                "name": w.name,
                "last_used_at": w.last_used_at,
                "session_count": w.session_count,
            }
            break
    if workspace_info is None:
        workspace_info = {
            "path": path,
            "name": os.path.basename(path),
            "last_used_at": "",
            "session_count": 0,
        }

    return {"ok": True, "workspace": workspace_info, "current_branch": current_branch}


@router.post("/api/workspaces/delete")
async def delete_workspace(body: dict) -> dict:
    """删除工作区及其所有会话。

    请求体：{"path": "..."}
    如果删除的是当前工作区，删除后清空当前工作区状态。
    """
    path = body.get("path", "")
    if not path:
        return {"ok": False, "error": "path is required"}

    deleted = server.state.session_store.delete_workspace(path)
    if not deleted:
        return {"ok": False, "error": "工作区不存在"}

    # 刷新工作区列表
    workspaces = server.state.session_store.list_workspaces()
    return {
        "ok": True,
        "workspaces": [
            {
                "path": w.path,
                "name": w.name,
                "last_used_at": w.last_used_at,
                "session_count": w.session_count,
            }
            for w in workspaces
        ],
    }
