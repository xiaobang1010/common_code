"""权限相关路由：权限决策回传与模式切换。"""

from __future__ import annotations

from fastapi import APIRouter

from server.state import permission_bridge

router = APIRouter()


# ---------------------------------------------------------------------------
# POST /api/permission - 回传权限决策
# ---------------------------------------------------------------------------


@router.post("/api/permission")
async def resolve_permission(body: dict) -> dict:
    """权限决策回传接口。

    请求体：{"request_id": "...", "decision": "allow"/"deny"/"always_allow"}
    返回：{"ok": true} 或 {"ok": false, "error": "request not found"}
    """
    request_id = body.get("request_id", "")
    decision = body.get("decision", "")
    ok = permission_bridge.resolve(request_id, decision)
    if ok:
        return {"ok": True}
    return {"ok": False, "error": "request not found"}


# ---------------------------------------------------------------------------
# POST /api/permission/mode - 切换权限模式
# ---------------------------------------------------------------------------


@router.post("/api/permission/mode")
async def set_permission_mode(body: dict) -> dict:
    """切换权限模式。

    请求体：{"mode": "default" | "full_access"}
    返回：{"ok": true, "mode": ...}
    """
    from startup.bootstrap.state import set_permission_mode as _set_mode
    from tools.utils.permissions.permissions import VALID_MODES

    mode = body.get("mode", "").strip()
    if mode not in VALID_MODES:
        return {"ok": False, "error": f"Invalid permission mode: {mode}. Valid: {VALID_MODES}"}

    _set_mode(mode)
    return {"ok": True, "mode": mode}
