"""子智能体相关路由：列出代理（内置+自定义）、创建/更新、删除。

写操作只作用于用户级目录 ~/.agent/agents/（项目级配置只读，
且其 permission_mode 在加载时被剥离，不可提权）。
"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

router = APIRouter()


# agent_type 只允许字母/数字/连字符，防路径穿越
_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


# ---------------------------------------------------------------------------
# GET /api/agents - 列出内置 + 自定义子智能体（含加载诊断）
# ---------------------------------------------------------------------------


@router.get("/api/agents")
async def list_agents() -> dict:
    """返回内置与自定义子智能体定义的只读字段，及自定义加载诊断。"""
    from tools.subagent.built_in_agents import get_built_in_agents
    from tools.subagent.loader import load_custom_agents

    def _to_dict(a) -> dict:
        return {
            "agent_type": a.agent_type,
            "when_to_use": a.when_to_use,
            "tools": a.tools,
            "disallowed_tools": a.disallowed_tools,
            "model": a.model,
            "max_turns": a.max_turns,
            "background": a.background,
            "source": a.source,
        }

    custom, diagnostics = load_custom_agents()
    return {
        "agents": [_to_dict(a) for a in get_built_in_agents()] + [_to_dict(a) for a in custom],
        "diagnostics": diagnostics,
    }


# ---------------------------------------------------------------------------
# POST /api/agents - 创建/更新用户级自定义子智能体
# ---------------------------------------------------------------------------


class AgentCreateRequest(BaseModel):
    """创建/更新自定义子智能体请求。"""

    name: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=500)
    system_prompt: str = ""
    tools: list[str] | None = None
    disallowed_tools: list[str] = Field(default_factory=list)
    model: str | None = None
    max_turns: int | None = Field(default=None, ge=1, le=200)
    background: bool = False


def _render_agent_md(req: AgentCreateRequest) -> str:
    """把请求渲染为 agent .md 文本（正文即系统提示词）。"""
    lines = [
        "---",
        f"name: {req.name}",
        f"description: {req.description}",
    ]
    if req.tools:
        lines.append(f"tools: [{', '.join(req.tools)}]")
    if req.disallowed_tools:
        lines.append(f"disallowed-tools: [{', '.join(req.disallowed_tools)}]")
    if req.model:
        lines.append(f"model: {req.model}")
    if req.max_turns is not None:
        lines.append(f"max-turns: {req.max_turns}")
    if req.background:
        lines.append("background: true")
    lines.append("---")
    lines.append("")
    lines.append(req.system_prompt or req.description)
    return "\n".join(lines) + "\n"


@router.post("/api/agents")
async def create_agent(req: AgentCreateRequest) -> JSONResponse:
    """创建或更新用户级自定义子智能体（写 ~/.agent/agents/<name>.md）。"""
    from tools.subagent.built_in_agents import get_built_in_agents
    from tools.subagent.loader import get_user_agents_dir

    if not _NAME_PATTERN.match(req.name):
        return JSONResponse(
            status_code=400,
            content={"error": "name 只允许字母/数字/连字符（不超过 64 字符）"},
        )
    # 内置类型不可覆盖
    if any(a.agent_type == req.name for a in get_built_in_agents()):
        return JSONResponse(
            status_code=409, content={"error": f"内置类型不可覆盖: {req.name}"}
        )

    target: Path = get_user_agents_dir() / f"{req.name}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_render_agent_md(req), encoding="utf-8")
    return {"ok": True, "file": str(target)}


# ---------------------------------------------------------------------------
# GET /api/team/teammates - 多智能体状态透出（替换纯占位页）
# ---------------------------------------------------------------------------


@router.get("/api/team/teammates")
async def list_team_teammates() -> dict:
    """返回团队列表、各团队成员与当前活跃 teammate 状态。

    数据来源：manager 的团队/成员配置 + lifecycle 的活跃注册表。
    """
    from tools.team.manager import get_members, list_teams
    from tools.team.lifecycle import get_active_teammates

    teams: list[dict] = []
    for name in list_teams():
        try:
            members = get_members(name)
        except Exception:  # noqa: BLE001 单个团队读取失败不影响整体
            members = []
        teams.append({"name": name, "members": members})

    active = [
        {
            "name": agent_name,
            "status": info.get("status"),
            "team_name": info.get("team_name"),
        }
        for agent_name, info in get_active_teammates().items()
    ]
    return {"teams": teams, "active_teammates": active}


# ---------------------------------------------------------------------------
# DELETE /api/agents/{name} - 删除用户级自定义子智能体
# ---------------------------------------------------------------------------


@router.delete("/api/agents/{name}")
async def delete_agent(name: str) -> JSONResponse:
    """删除用户级自定义子智能体（内置与项目级不可删）。"""
    from tools.subagent.built_in_agents import get_built_in_agents
    from tools.subagent.loader import get_user_agents_dir

    if any(a.agent_type == name for a in get_built_in_agents()):
        return JSONResponse(status_code=403, content={"error": "内置类型不可删除"})
    if not _NAME_PATTERN.match(name):
        return JSONResponse(status_code=400, content={"error": "非法 name"})

    target = get_user_agents_dir() / f"{name}.md"
    if not target.is_file():
        return JSONResponse(status_code=404, content={"error": f"不存在: {name}"})
    target.unlink()
    return {"ok": True}
