"""任务分组管理路由：列表、创建、更新、删除。

分组只是任务的视图标签：删除分组只解除成员归属，不动任务记录本身。
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

import server.state

router = APIRouter()


@router.get("/api/session-groups")
def list_session_groups() -> dict:
    """列出所有自定义任务分组。

    返回 {"groups": [{id, name, color, created_at}]}（按创建时间升序）
    """
    groups = server.state.session_store.list_task_groups()
    return {
        "groups": [
            {"id": g.id, "name": g.name, "color": g.color, "created_at": g.created_at}
            for g in groups
        ]
    }


@router.post("/api/session-groups")
def create_session_group(body: dict) -> dict:
    """创建任务分组。

    请求体：{"name": "...", "color": "可选"}
    name 必填（去空白后非空），对齐既有接口必填校验风格。
    """
    name = str(body.get("name", "")).strip()
    if not name:
        return {"ok": False, "error": "name is required"}
    color = str(body.get("color", "") or "")
    group = server.state.session_store.create_task_group(name, color)
    return {
        "ok": True,
        "group": {
            "id": group.id,
            "name": group.name,
            "color": group.color,
            "created_at": group.created_at,
        },
    }


@router.patch("/api/session-groups/{group_id}")
def update_session_group(group_id: str, body: dict) -> dict:
    """更新任务分组：支持 name（重命名）与 color（改色）。"""
    name = body.get("name")
    color = body.get("color")
    if name is None and color is None:
        return {"ok": False, "error": "nothing to update"}
    if name is not None and not str(name).strip():
        return {"ok": False, "error": "name cannot be empty"}
    updated = server.state.session_store.update_task_group(
        group_id,
        name=str(name).strip() if name is not None else None,
        color=str(color) if color is not None else None,
    )
    if not updated:
        return JSONResponse(status_code=404, content={"ok": False, "error": "group not found"})
    return {"ok": True}


@router.delete("/api/session-groups/{group_id}")
def delete_session_group(group_id: str) -> dict:
    """删除任务分组：成员任务的 group_id 置空回"未分组"，不删任务。"""
    deleted = server.state.session_store.delete_task_group(group_id)
    if not deleted:
        return JSONResponse(status_code=404, content={"ok": False, "error": "group not found"})
    return {"ok": True}
