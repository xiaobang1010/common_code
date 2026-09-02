"""执行底座配套测试 - 类型解析、模型解析链、配置端点、AGENTS.md 注入、操控杆。"""

from __future__ import annotations

import asyncio
import time

import pytest

from startup.config.types import SubagentsConfig
from tools.protocol import ToolUseContext
from tools.subagent.context import AgentDefinition, create_subagent_context
from tools.subagent.registry import (
    STATUS_COMPLETED,
    STATUS_RUNNING,
    SubagentTaskRegistry,
)

_test_registry = SubagentTaskRegistry()


@pytest.fixture
def substrate(monkeypatch):
    monkeypatch.setattr(
        "tools.subagent.registry.get_subagent_registry", lambda: _test_registry
    )
    _test_registry._tasks.clear()
    return monkeypatch


# ---------------------------------------------------------------------------
# 8.5 类型解析：模糊匹配 / 歧义 / 未命中
# ---------------------------------------------------------------------------


def test_resolver_exact_fuzzy_ambiguous_not_found(monkeypatch):
    from tools.subagent import resolver
    from tools.subagent.resolver import resolve_agent_type

    agents = [
        AgentDefinition(agent_type="general-purpose", when_to_use="通用"),
        AgentDefinition(agent_type="Explore", when_to_use="搜索"),
        AgentDefinition(agent_type="review-validate", when_to_use="审核"),
    ]
    monkeypatch.setattr(resolver, "get_all_agent_definitions", lambda: agents)

    # 精确
    assert resolve_agent_type("Explore").kind == "matched"
    # 归一化模糊（大小写 + 分隔符）
    r = resolve_agent_type("general purpose")
    assert r.kind == "matched" and r.agent.agent_type == "general-purpose"
    r = resolve_agent_type("REVIEW_VALIDATE")
    assert r.kind == "matched" and r.agent.agent_type == "review-validate"

    # 歧义：两个代理归一化同名（用第三种写法查询，避开精确命中）
    agents_dup = agents + [AgentDefinition(agent_type="review_validate", when_to_use="另一个")]
    monkeypatch.setattr(resolver, "get_all_agent_definitions", lambda: agents_dup)
    r = resolve_agent_type("review validate")
    assert r.kind == "ambiguous"
    assert set(r.matches) == {"review-validate", "review_validate"}
    assert "exact name" in r.error_text("review validate")

    # 未命中：错误文本带全部可用类型
    r = resolve_agent_type("不存在")
    assert r.kind == "not_found"
    text = r.error_text("不存在")
    assert "Explore" in text and "general-purpose" in text


# ---------------------------------------------------------------------------
# 8.5 模型解析链
# ---------------------------------------------------------------------------


def test_model_resolution_chain(monkeypatch):
    from startup.config import GlobalConfig

    cfg = GlobalConfig(subagents=SubagentsConfig(
        model_overrides={"Explore": "override-model"},
        default_model="default-model",
    ))
    monkeypatch.setattr("startup.config.get_global_config", lambda: cfg)

    main = "main-model"
    # 1. profile 显式指定最优先
    a = AgentDefinition(agent_type="Explore", when_to_use="t", model="profile-model")
    assert a.resolve_model(main) == "profile-model"
    # 2. 配置按类型覆盖
    b = AgentDefinition(agent_type="Explore", when_to_use="t", model="inherit")
    assert b.resolve_model(main) == "override-model"
    # 3. 配置默认模型
    c = AgentDefinition(agent_type="other", when_to_use="t")
    assert c.resolve_model(main) == "default-model"
    # 4. 回退主循环模型
    monkeypatch.setattr(
        "startup.config.get_global_config",
        lambda: GlobalConfig(subagents=SubagentsConfig()),
    )
    assert c.resolve_model(main) == main


# ---------------------------------------------------------------------------
# 8.5 inject_agents_md
# ---------------------------------------------------------------------------


def test_inject_agents_md_on_off_missing(monkeypatch, tmp_path):
    from tools.subagent.context import build_subagent_system_prompt

    (tmp_path / "AGENTS.md").write_text("工作区规范内容", encoding="utf-8")
    monkeypatch.setattr("server.paths.effective_root", lambda: str(tmp_path))

    on = AgentDefinition(agent_type="a", when_to_use="t", system_prompt="基础提示词", inject_agents_md=True)
    off = AgentDefinition(agent_type="b", when_to_use="t", system_prompt="基础提示词", inject_agents_md=False)

    assert "工作区规范内容" in build_subagent_system_prompt(on)
    assert "工作区规范内容" not in build_subagent_system_prompt(off)

    # 文件缺失：静默跳过，返回基础提示词
    monkeypatch.setattr("server.paths.effective_root", lambda: str(tmp_path / "empty"))
    assert build_subagent_system_prompt(on) == "基础提示词"


# ---------------------------------------------------------------------------
# 8.5 配置端点读写与校验
# ---------------------------------------------------------------------------


@pytest.fixture
def config_client(monkeypatch, tmp_path):
    """隔离全局配置文件路径后的 TestClient。"""
    import startup.config as config_mod

    config_path = tmp_path / "config.json"
    monkeypatch.setattr(config_mod, "get_global_config_path", lambda: config_path)
    monkeypatch.setattr(config_mod, "_global_config_cache", None)
    monkeypatch.setattr(config_mod, "_global_config_cache_mtime", 0.0)
    # 允许读取配置（测试环境未走 setup 初始化）
    monkeypatch.setattr(config_mod, "_config_reading_allowed", True)

    from fastapi.testclient import TestClient
    from server.app import app

    return TestClient(app)


def test_subagents_config_endpoint_roundtrip(config_client):
    resp = config_client.get("/api/config/subagents")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["subagents"]["autoBackgroundMs"] == 60000

    resp = config_client.post(
        "/api/config/subagents",
        json={"autoBackgroundMs": 30000, "maxTurnsDefault": 80, "defaultModel": "m2"},
    )
    assert resp.json()["ok"] is True

    body = config_client.get("/api/config/subagents").json()
    assert body["subagents"]["autoBackgroundMs"] == 30000
    assert body["subagents"]["maxTurnsDefault"] == 80
    assert body["subagents"]["defaultModel"] == "m2"
    # 未传字段保持默认
    assert body["subagents"]["inactivityTimeoutMs"] == 300000


def test_subagents_config_endpoint_rejects_invalid(config_client):
    resp = config_client.post("/api/config/subagents", json={"autoBackgroundMs": -1})
    assert resp.json()["ok"] is False
    resp = config_client.post("/api/config/subagents", json={"tokenBudgetDefault": "abc"})
    assert resp.json()["ok"] is False
    resp = config_client.post("/api/config/subagents", json={"modelOverrides": {"a": 1}})
    assert resp.json()["ok"] is False


# ---------------------------------------------------------------------------
# 8.5 插件代理命名空间加载
# ---------------------------------------------------------------------------


def test_plugin_agents_namespaced_loading(monkeypatch, tmp_path):
    """插件 agents/*.md 以 <插件名>:<代理名> 命名空间加载，禁用插件不提供。"""
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))

    # 构造带 agents/ 的插件
    plugin_dir = home / ".agent" / "plugins" / "demo"
    (plugin_dir / ".agent-plugin").mkdir(parents=True)
    (plugin_dir / ".agent-plugin" / "plugin.json").write_text(
        '{"name": "demo", "version": "1.0.0", "kind": "standard"}', encoding="utf-8"
    )
    (plugin_dir / "agents").mkdir()
    (plugin_dir / "agents" / "coder.md").write_text(
        "---\nname: coder\ndescription: 编码代理\ntools: [Read]\n---\n\n正文",
        encoding="utf-8",
    )
    # frontmatter 缺必填字段的文件产生诊断而非崩溃
    (plugin_dir / "agents" / "bad.md").write_text(
        "---\nname: onlyname\n---\n\n正文", encoding="utf-8"
    )

    from startup.plugins.loader import clear_cache

    clear_cache()
    try:
        from tools.subagent.plugin_agents import get_plugin_agent_definitions

        agents = get_plugin_agent_definitions()
        assert len(agents) == 1  # bad.md 被诊断跳过
        a = agents[0]
        assert a.agent_type == "demo:coder"
        assert a.source == "plugin"
        assert a.tools == ["Read"]

        # 解析器命中命名空间代理；未命中错误文本也包含它
        from tools.subagent.resolver import resolve_agent_type

        r = resolve_agent_type("demo:coder")
        assert r.kind == "matched" and r.agent.agent_type == "demo:coder"
        r2 = resolve_agent_type("不存在")
        assert r2.kind == "not_found" and "demo:coder" in r2.available

        # 禁用插件后不再提供代理（经配置文件的 disabled 列表）
        (home / ".agent" / "config.json").write_text(
            '{"plugins": {"disabled": ["demo"]}}', encoding="utf-8"
        )
        import startup.config as config_mod

        monkeypatch.setattr(config_mod, "_config_reading_allowed", True)
        monkeypatch.setattr(config_mod, "_global_config_cache", None)
        monkeypatch.setattr(config_mod, "_global_config_cache_mtime", 0.0)
        clear_cache()
        assert get_plugin_agent_definitions() == []
    finally:
        clear_cache()


# ---------------------------------------------------------------------------
# 8.6 GetSubagentOutput block 与增量输出
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_output_block_waits_terminal(substrate):
    """block 模式：等待终态后返回最终结果。"""
    from tools.subagent.task_tools import GetSubagentOutputInput, _get_output

    agent_def = AgentDefinition(agent_type="general-purpose", when_to_use="t")
    ctx = create_subagent_context(
        parent_context=None, agent_def=agent_def, main_loop_model="m",
        agent_id="agent_blk", prompt="t",
    )
    _test_registry.register("agent_blk", ctx)

    async def _finish_later():
        await asyncio.sleep(0.2)
        _test_registry.set_result(
            "agent_blk", status=STATUS_COMPLETED, final_text="最终输出"
        )

    asyncio.get_event_loop().create_task(_finish_later())

    result = await _get_output(
        GetSubagentOutputInput(agent_id="agent_blk", block=True, timeout_ms=2000),
        ToolUseContext(),
    )
    assert "最终输出" in result.content
    assert result.metadata.get("source") == "final"
    assert result.metadata.get("not_ready") is False


@pytest.mark.asyncio
async def test_get_output_block_timeout_marks_not_ready(substrate):
    """block 超时：返回中间输出并标注 not_ready。"""
    from tools.subagent.task_tools import GetSubagentOutputInput, _get_output
    from tools.subagent.transcript import append_task_output

    agent_def = AgentDefinition(agent_type="general-purpose", when_to_use="t")
    ctx = create_subagent_context(
        parent_context=None, agent_def=agent_def, main_loop_model="m",
        agent_id="agent_tmo", prompt="t",
    )
    _test_registry.register("agent_tmo", ctx)
    append_task_output("agent_tmo", "阶段性输出")

    t0 = time.monotonic()
    result = await _get_output(
        GetSubagentOutputInput(agent_id="agent_tmo", block=True, timeout_ms=300),
        ToolUseContext(),
    )
    elapsed = time.monotonic() - t0
    assert elapsed >= 0.25  # 确实等到了超时
    assert result.metadata.get("not_ready") is True
    assert "阶段性输出" in result.content
    assert result.metadata.get("source") == "incremental"


@pytest.mark.asyncio
async def test_get_output_without_block_keeps_legacy(substrate):
    """不传 block：行为与原先一致（立即返回当前输出）。"""
    from tools.subagent.task_tools import GetSubagentOutputInput, _get_output

    agent_def = AgentDefinition(agent_type="general-purpose", when_to_use="t")
    ctx = create_subagent_context(
        parent_context=None, agent_def=agent_def, main_loop_model="m",
        agent_id="agent_nb", prompt="t",
    )
    _test_registry.register("agent_nb", ctx)
    _test_registry.set_result("agent_nb", status=STATUS_COMPLETED, final_text="完成")

    result = await _get_output(
        GetSubagentOutputInput(agent_id="agent_nb"), ToolUseContext(),
    )
    assert "完成" in result.content
    assert result.metadata.get("not_ready") is False


# ---------------------------------------------------------------------------
# 8.6 详情端点预算字段与列表历史重建
# ---------------------------------------------------------------------------


def test_detail_endpoint_budget_and_list_history(monkeypatch, tmp_path):
    """详情含预算信息；列表融合注册表运行态与子会话历史。"""
    from fastapi.testclient import TestClient
    from server.app import app
    from session.store import SessionStore
    import server.state
    import server.routers.subagents.routes as sub_routes

    # 注册表：一个运行中任务（带预算上下文）
    registry = SubagentTaskRegistry()
    agent_def = AgentDefinition(agent_type="general-purpose", when_to_use="t")
    ctx = create_subagent_context(
        parent_context=None, agent_def=agent_def, main_loop_model="m",
        agent_id="agent_run", prompt="t",
    )
    ctx.max_turns = 30
    ctx.token_budget = 50000
    registry.register("agent_run", ctx, parent_session_id="sess_p")
    monkeypatch.setattr(
        "tools.subagent.registry.get_subagent_registry", lambda: registry
    )

    # 会话存储：一条终态子会话（重启后历史）
    store = SessionStore(db_path=tmp_path / "sessions.db")
    store.create_session(
        str(tmp_path), title="历史任务", session_id="subagent_agent_hist",
        origin="subagent", parent_session_id="sess_p",
    )
    store.merge_session_agent_meta(
        "subagent_agent_hist",
        {"agent_id": "agent_hist", "agent_type": "Explore", "status": "completed"},
    )
    monkeypatch.setattr(server.state, "session_store", store)

    client = TestClient(app)

    # 详情：预算字段
    detail = client.get("/api/subagents/agent_run").json()
    assert detail["budget"]["max_turns"] == 30
    assert detail["budget"]["token_budget"] == 50000
    assert detail["child_session_id"] == "subagent_agent_run" or detail["child_session_id"] == ""

    # 列表：运行态 + 历史（去重），历史条目含提升标记字段
    listing = client.get("/api/subagents").json()["subagents"]
    ids = {item["agent_id"] for item in listing}
    assert "agent_run" in ids and "agent_hist" in ids
    hist = next(i for i in listing if i["agent_id"] == "agent_hist")
    assert hist["origin"] == "history"
    assert hist["child_session_id"] == "subagent_agent_hist"
    assert hist["promoted"] is False
    run = next(i for i in listing if i["agent_id"] == "agent_run")
    assert run["origin"] == "registry"
