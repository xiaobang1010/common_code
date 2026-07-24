"""技能相关路由：列出、创建、导入、刷新、删除。"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


# ---------------------------------------------------------------------------
# GET /api/skills - 获取可用 skill 列表（供前端命令补全）
# ---------------------------------------------------------------------------


@router.get("/api/skills")
async def list_skills() -> dict:
    """返回所有用户可调用的 skill 列表（含来源与完整元数据）。

    返回：{"skills": [{"name", "description", "when_to_use", "source",
                        "source_label", "allowed_tools", "disable_model_invocation",
                        "user_invocable", "skill_root"}, ...]}
    """
    from tools.skills.bundled import get_all_skills
    from tools.skills.loader import classify_skill_source

    skills = get_all_skills()
    return {
        "skills": [
            {
                "name": s.name,
                "description": s.description,
                "when_to_use": s.when_to_use or "",
                "source": s.source,
                "source_label": classify_skill_source(s),
                "allowed_tools": s.allowed_tools,
                "disable_model_invocation": s.disable_model_invocation,
                "user_invocable": s.user_invocable,
                "skill_root": s.skill_root,
            }
            for s in skills
            if s.is_user_invocable()
        ]
    }


# ---------------------------------------------------------------------------
# POST /api/skills/create - 新建技能
# ---------------------------------------------------------------------------


@router.post("/api/skills/create")
async def create_skill(body: dict) -> dict:
    """新建技能。请求体：{"name", "description", "when_to_use", "allowed_tools"}"""
    from tools.skills.loader import create_skill_file

    name = body.get("name", "").strip()
    description = body.get("description", "").strip()
    when_to_use = body.get("when_to_use", "").strip()
    allowed_tools = body.get("allowed_tools") or None

    if not name or not description:
        return {"ok": False, "error": "name 和 description 必填"}

    try:
        create_skill_file(name, description, when_to_use, allowed_tools)
        return {"ok": True, "name": name}
    except ValueError as e:
        return {"ok": False, "error": str(e)}


# ---------------------------------------------------------------------------
# POST /api/skills/import - 导入技能
# ---------------------------------------------------------------------------


@router.post("/api/skills/import")
async def import_skill(body: dict) -> dict:
    """导入技能。请求体：{"name", "content"}"""
    from tools.skills.loader import import_skill_file

    name = body.get("name", "").strip()
    content = body.get("content", "")

    if not name or not content:
        return {"ok": False, "error": "name 和 content 必填"}

    try:
        import_skill_file(name, content)
        return {"ok": True, "name": name}
    except ValueError as e:
        return {"ok": False, "error": str(e)}


# ---------------------------------------------------------------------------
# POST /api/skills/refresh - 刷新技能缓存
# ---------------------------------------------------------------------------


@router.post("/api/skills/refresh")
async def refresh_skills() -> dict:
    """刷新技能缓存，重新扫描文件系统。"""
    from tools.skills.loader import clear_cache
    clear_cache()
    return {"ok": True}


# ---------------------------------------------------------------------------
# POST /api/skills/delete - 删除用户级技能
# ---------------------------------------------------------------------------


@router.post("/api/skills/delete")
async def delete_skill(body: dict) -> dict:
    """删除用户级技能。请求体：{"name"}"""
    from tools.skills.loader import delete_skill_file

    name = body.get("name", "").strip()
    if not name:
        return {"ok": False, "error": "name 必填"}

    try:
        delete_skill_file(name)
        return {"ok": True}
    except ValueError as e:
        return {"ok": False, "error": str(e)}
