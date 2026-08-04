"""子智能体相关路由：列出内置代理。"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


# ---------------------------------------------------------------------------
# GET /api/agents - 列出内置子智能体（只读）
# ---------------------------------------------------------------------------


@router.get("/api/agents")
async def list_agents() -> dict:
    """返回内置子智能体定义的只读字段。"""
    from tools.subagent.built_in_agents import get_built_in_agents

    agents = []
    for a in get_built_in_agents():
        agents.append({
            "agent_type": a.agent_type,
            "when_to_use": a.when_to_use,
            "tools": a.tools,
            "disallowed_tools": a.disallowed_tools,
            "model": a.model,
            "max_turns": a.max_turns,
            "background": a.background,
            "source": a.source,
        })
    return {"agents": agents}
