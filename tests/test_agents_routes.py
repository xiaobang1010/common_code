"""agents 路由写端点测试 - 创建/列表/删除、内置保护、诊断透出。"""

from __future__ import annotations

import pytest


@pytest.fixture
def user_dir(tmp_path, monkeypatch):
    """用户级代理目录指到临时目录。"""
    import tools.subagent.loader as loader_mod
    d = tmp_path / "agents"
    monkeypatch.setattr(loader_mod, "get_user_agents_dir", lambda: d)
    return d


@pytest.mark.asyncio
async def test_create_list_delete_roundtrip(user_dir):
    """创建 -> 列表可见 -> 删除 -> 消失。"""
    from server.routers.agents import routes

    req = routes.AgentCreateRequest(
        name="reviewer", description="审查代码",
        system_prompt="你是审查员。", tools=["Read", "Grep"], max_turns=5,
    )
    created = await routes.create_agent(req)
    assert created["ok"] is True
    assert (user_dir / "reviewer.md").is_file()

    listing = await routes.list_agents()
    names = [a["agent_type"] for a in listing["agents"]]
    assert "reviewer" in names and "general-purpose" in names
    reviewer = next(a for a in listing["agents"] if a["agent_type"] == "reviewer")
    assert reviewer["source"] == "user"
    assert reviewer["tools"] == ["Read", "Grep"]

    deleted = await routes.delete_agent("reviewer")
    assert deleted["ok"] is True
    listing2 = await routes.list_agents()
    assert "reviewer" not in [a["agent_type"] for a in listing2["agents"]]


@pytest.mark.asyncio
async def test_create_rejects_builtin_and_bad_name(user_dir):
    """内置类型不可覆盖；非法 name（路径穿越）被拒。"""
    from server.routers.agents import routes

    dup = await routes.create_agent(
        routes.AgentCreateRequest(name="Explore", description="x")
    )
    assert dup.status_code == 409

    bad = await routes.create_agent(
        routes.AgentCreateRequest(name="../evil", description="x")
    )
    assert bad.status_code == 400


@pytest.mark.asyncio
async def test_delete_builtin_forbidden(user_dir):
    """内置类型不可删除。"""
    from server.routers.agents import routes

    result = await routes.delete_agent("general-purpose")
    assert result.status_code == 403


@pytest.mark.asyncio
async def test_list_includes_diagnostics(user_dir):
    """加载失败的诊断透出到列表响应。"""
    from server.routers.agents import routes

    user_dir.mkdir(parents=True, exist_ok=True)
    (user_dir / "broken.md").write_text("no frontmatter", encoding="utf-8")
    listing = await routes.list_agents()
    codes = [d["code"] for d in listing["diagnostics"]]
    assert "agent_missing_frontmatter" in codes
