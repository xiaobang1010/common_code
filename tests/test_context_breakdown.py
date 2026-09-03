"""上下文分类 token 估算与暴露测试。

覆盖 build_context_breakdown 的分类口径（各分类之和 = total、MCP 前缀归类、
技能段/清单归 skills、记忆召回归 other）、空输入健壮性，
以及 serialize_event 透出与 /api/state 返回链路。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import query.services.context_metrics as cm
from prompts.system.sections import SystemPromptSection
from query.services.api.llm import StreamEvent
from query.services.context_metrics import build_context_breakdown


@pytest.fixture(autouse=True)
def fake_schema(monkeypatch):
    """工具 schema 转换打桩为固定结构，避免依赖 pydantic 模型构建。"""
    monkeypatch.setattr(
        cm, "tool_to_api_schema",
        lambda tool: {"function": {"name": tool.name, "parameters": {"x": "p" * 40}}},
    )


def _section(name: str, content: str, cache_scope: str | None = None) -> SystemPromptSection:
    return SystemPromptSection(content=content, cache_scope=cache_scope, name=name)


def _tool(name: str) -> SimpleNamespace:
    return SimpleNamespace(name=name)


def test_categories_sum_to_total():
    """各分类之和等于 total，分类值均为正。"""
    breakdown = build_context_breakdown(
        sections=[
            _section("static_sections", "S" * 80, "static"),
            _section("skill_guidance", "K" * 40),
        ],
        tools=[_tool("Read"), _tool("mcp__demo__echo")],
        history_messages=[{"role": "user", "content": "H" * 40}],
        skill_listing_text="L" * 40,
        recall_text="R" * 40,
    )
    cats = {k: v for k, v in breakdown.items() if k != "total"}
    assert sum(cats.values()) == breakdown["total"]
    assert all(v > 0 for v in cats.values())


def test_mcp_prefix_and_skills_grouping():
    """`mcp__` 前缀工具归 mcp_tools；技能段与清单合并归 skills；召回归 other。"""
    breakdown = build_context_breakdown(
        sections=[
            _section("static_sections", "S" * 80, "static"),
            _section("skill_guidance", "K" * 40),
        ],
        tools=[_tool("Read"), _tool("mcp__demo__echo")],
        history_messages=[{"role": "user", "content": "H" * 40}],
        skill_listing_text="L" * 40,
        recall_text="R" * 40,
    )
    assert "mcp_tools" in breakdown and "system_tools" in breakdown
    # skills = 技能指导段 + 清单注入（两段各 40 字符 -> 各 max(1, 40//4)=10）
    assert breakdown["skills"] == 20
    assert breakdown["other"] == 10
    assert breakdown["system_prompt"] == 20


def test_empty_inputs_no_error():
    """全空输入：仅 total=0，不抛错。"""
    breakdown = build_context_breakdown(
        sections=[], tools=[], history_messages=[],
        skill_listing_text=None, recall_text=None,
    )
    assert breakdown == {"total": 0}


def test_zero_categories_omitted():
    """占比为 0 的分类不出现在结果里。"""
    breakdown = build_context_breakdown(
        sections=[_section("static_sections", "S" * 80, "static")],
        tools=[], history_messages=[],
    )
    assert set(breakdown.keys()) == {"system_prompt", "total"}


def test_serialize_event_passes_breakdown():
    """serialize_event 白名单透出 breakdown 字段。"""
    from server.routers.chat.routes import serialize_event

    ev = StreamEvent(type="context_breakdown", breakdown={"system_prompt": 10, "total": 10})
    out = serialize_event(ev)
    assert out["event_type"] == "context_breakdown"
    assert out["breakdown"] == {"system_prompt": 10, "total": 10}


def test_api_state_returns_breakdown(monkeypatch):
    """/api/state 响应携带 AppState 中的 context_breakdown。"""
    import server.state
    from server.routers.chat.routes import get_state
    from startup.state.app_state import AppState, AppStateProvider

    state = AppState()
    state.context_breakdown = {"system_tools": 100, "total": 100}
    monkeypatch.setattr(server.state, "app_state", AppStateProvider(state))
    monkeypatch.setattr(server.state, "engine", SimpleNamespace(mutable_messages=[]))
    monkeypatch.setattr(server.state, "engine_session_id", None)
    monkeypatch.setattr(server.state, "running_runs", {})

    result = asyncio.run(get_state())
    assert result["context_breakdown"] == {"system_tools": 100, "total": 100}
