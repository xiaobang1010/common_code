"""星形协调测试 - 工具池三层封堵、循环刷新守卫、子→父汇报通道、父会话标识链路。"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from tools.protocol import Tool, ToolUseContext
from tools.subagent import notify
from tools.subagent.context import AgentDefinition, create_subagent_context
from tools.subagent.registry import SubagentTaskRegistry

_test_registry = SubagentTaskRegistry()

# 星形拓扑下子代理不得持有的横向/派生工具
_LATERAL = {"SendMessage", "GetSubagentOutput", "StopSubagent", "Agent"}


@pytest.fixture
def star(monkeypatch, tmp_path):
    """桩环境：独立注册表 + 清空通知队列 + 临时工作区 + 打桩外部依赖。"""
    _test_registry._tasks.clear()
    notify._pending_notifications.clear()

    monkeypatch.setattr(
        "tools.subagent.registry.get_subagent_registry", lambda: _test_registry
    )
    from startup.config import GlobalConfig
    from startup.config.types import SubagentsConfig

    monkeypatch.setattr(
        "startup.config.get_global_config",
        lambda: GlobalConfig(subagents=SubagentsConfig()),
    )
    monkeypatch.setattr("query.services.api.client.get_default_model", lambda: "m")
    import tools as tools_pkg

    monkeypatch.setattr(tools_pkg, "get_tools", lambda *a, **k: [])
    monkeypatch.setattr("server.paths.effective_root", lambda: str(tmp_path))
    import tools.subagent.transcript as transcript

    monkeypatch.setattr(transcript, "record_sidechain_transcript", lambda *a, **k: None)
    monkeypatch.setattr(transcript, "write_agent_metadata", lambda **k: None)
    monkeypatch.setattr(transcript, "save_full_result", lambda *a, **k: "/tmp/fake.txt")
    monkeypatch.setattr(transcript, "append_task_output", lambda *a, **k: None)
    yield monkeypatch
    notify._pending_notifications.clear()


def _agent_def(**kw) -> AgentDefinition:
    defaults = dict(agent_type="general-purpose", when_to_use="测试")
    defaults.update(kw)
    return AgentDefinition(**defaults)


# ---------------------------------------------------------------------------
# 注册层与调用点：工具池封堵（真实 get_tools，不桩掉）
# ---------------------------------------------------------------------------


def test_real_main_pool_keeps_lateral_tools():
    """主循环池不受影响：横向管理与续聊工具都在，且没有汇报工具。"""
    from tools import get_tools

    names = {t.name for t in get_tools()}
    assert _LATERAL <= names
    assert "RespondToCoordinator" not in names


def test_real_general_subagent_pool_star_shape():
    """general-purpose 子代理池：横向工具全无，只剩对父汇报通道。"""
    from tools import ToolContextFilter, get_tools

    names = {
        t.name for t in get_tools(ToolContextFilter.for_subagent("general-purpose"))
    }
    assert not names & _LATERAL
    assert "RespondToCoordinator" in names


def test_real_explore_pool_unchanged():
    """Explore 仍恰为六工具白名单，汇报工具不在白名单即不出现。"""
    from tools import ToolContextFilter, get_tools

    names = {t.name for t in get_tools(ToolContextFilter.for_subagent("Explore"))}
    assert names == {"Bash", "Glob", "Grep", "Read", "WebFetch", "TodoWrite"}


def test_real_teammate_pool_unchanged():
    """teammate 保留 team 邮箱 SendMessage，不混入子代理侧工具。"""
    from tools import ToolContextFilter, get_tools

    names = {
        t.name
        for t in get_tools(
            ToolContextFilter(
                is_subagent=True, is_teammate=True, agent_type="general-purpose"
            )
        )
    }
    assert "SendMessage" in names
    assert "RespondToCoordinator" not in names
    assert not names & {"GetSubagentOutput", "StopSubagent"}


def test_unknown_agent_type_sub_context_still_sealed():
    """未知代理类型（自定义 .md 未加载）兜底只移 Agent 时，注册层排除独立成立。"""
    from tools import ToolContextFilter, get_tools

    names = {
        t.name
        for t in get_tools(
            ToolContextFilter(is_subagent=True, agent_type="custom-md-probe")
        )
    }
    assert not names & _LATERAL
    assert "RespondToCoordinator" in names


# ---------------------------------------------------------------------------
# 主循环每轮刷新守卫（子代理全程锁定派生时池）
# ---------------------------------------------------------------------------


@dataclass
class _ToolRoundDeps:
    """前两轮发起不存在的工具调用使循环走到刷新点，第三轮收尾。"""

    calls: int = 0
    observed_tools: list = field(default_factory=list)

    def get_uuid(self) -> str:
        return "u"

    async def call_model(self, messages=None, tools=None, **kw):
        from query.services.api.llm import StreamEvent

        self.calls += 1
        self.observed_tools.append([getattr(t, "name", str(t)) for t in (tools or [])])
        if self.calls <= 2:
            yield StreamEvent(type="content", content=f"轮{self.calls}")
            yield StreamEvent(
                type="tool_call_delta", tool_call_index=0,
                tool_call_id=f"call_{self.calls}", tool_call_name="NoSuchTool",
            )
            yield StreamEvent(
                type="tool_call_delta", tool_call_index=0, tool_call_arguments="{}",
            )
            yield StreamEvent(type="done", finish_reason="tool_calls")
        else:
            yield StreamEvent(type="content", content="完成")
            yield StreamEvent(type="done", finish_reason="stop")
        yield StreamEvent(type="usage", usage={"total_tokens": 10})


def _sentinel_tool() -> Tool:
    from pydantic import BaseModel

    from tools.protocol import ToolResult, build_tool

    class _EmptyInput(BaseModel):
        pass

    async def _noop(inp, context):
        return ToolResult(content="noop")

    return build_tool(
        name="SentinelOnly", description="哨兵", input_schema=_EmptyInput,
        execute=_noop, prompt="", is_read_only=True,
    )


def _make_guard_engine(deps):
    from query.engine import QueryEngine, build_engine_config

    config = build_engine_config(model="fake", tools=[_sentinel_tool()], max_turns=10, deps=deps)
    return QueryEngine(config, initial_messages=[{"role": "user", "content": "t"}])


@pytest.mark.asyncio
async def test_loop_refresh_guard_locks_subagent_pool(monkeypatch):
    """子代理上下文：每轮整体刷新被跳过，模型请求始终看到派生时的哨兵池。"""
    from query.config import build_query_config
    from query.loop import query_loop

    import query.loop as loop_mod

    refresh_calls: list[int] = []
    monkeypatch.setattr(loop_mod, "get_tools", lambda: refresh_calls.append(1) or [])

    deps = _ToolRoundDeps()
    engine = _make_guard_engine(deps)
    tctx = ToolUseContext(tool_use_id="agent_guard01")
    async for _ev in query_loop(
        engine, build_query_config(session_id="s_guard_sub"), tool_use_context=tctx
    ):
        pass

    assert refresh_calls == []
    assert deps.calls == 3
    assert all(names == ["SentinelOnly"] for names in deps.observed_tools)


@pytest.mark.asyncio
async def test_loop_refresh_still_applies_for_main_loop(monkeypatch):
    """主循环（无子上下文）刷新不受影响：第二轮起模型看到刷新后的池。"""
    from query.config import build_query_config
    from query.loop import query_loop

    import query.loop as loop_mod

    refresh_calls: list[int] = []
    monkeypatch.setattr(loop_mod, "get_tools", lambda: refresh_calls.append(1) or [])

    deps = _ToolRoundDeps()
    engine = _make_guard_engine(deps)
    async for _ev in query_loop(engine, build_query_config(session_id="s_guard_main")):
        pass

    assert refresh_calls
    assert deps.observed_tools[1] == []


@pytest.mark.asyncio
async def test_loop_executor_context_carries_parent_session_id():
    """端到端：父会话标识必须穿过每轮执行器上下文重建抵达工具（真实链路探针）。

    RespondToCoordinator 消费的是执行器重建后的 context 而非 runner 传入的
    原始上下文，此用例守这条真实链路，防止字段在 loop 重建处丢失。
    """
    from pydantic import BaseModel

    from query.config import build_query_config
    from query.engine import QueryEngine, build_engine_config
    from query.loop import query_loop
    from query.services.api.llm import StreamEvent
    from tools.protocol import ToolResult, build_tool

    seen: dict[str, str] = {}

    class _ProbeInput(BaseModel):
        pass

    async def _probe(inp, context):
        seen["parent"] = getattr(context, "parent_session_id", "")
        seen["agent"] = getattr(context, "tool_use_id", "")
        return ToolResult(content="ok")

    probe = build_tool(
        name="ProbeTool", description="探针", input_schema=_ProbeInput,
        execute=_probe, prompt="", is_read_only=True,
    )

    @dataclass
    class _ProbeDeps:
        calls: int = 0

        def get_uuid(self) -> str:
            return "u"

        async def call_model(self, messages=None, tools=None, **kw):
            self.calls += 1
            if self.calls == 1:
                yield StreamEvent(
                    type="tool_call_delta", tool_call_index=0,
                    tool_call_id="c1", tool_call_name="ProbeTool",
                )
                yield StreamEvent(
                    type="tool_call_delta", tool_call_index=0, tool_call_arguments="{}",
                )
                yield StreamEvent(type="done", finish_reason="tool_calls")
            else:
                yield StreamEvent(type="content", content="完成")
                yield StreamEvent(type="done", finish_reason="stop")
            yield StreamEvent(type="usage", usage={"total_tokens": 5})

    deps = _ProbeDeps()
    config = build_engine_config(model="fake", tools=[probe], max_turns=10, deps=deps)
    engine = QueryEngine(config, initial_messages=[{"role": "user", "content": "t"}])
    tctx = ToolUseContext(tool_use_id="agent_e2e01", parent_session_id="sess_e2e")
    async for _ev in query_loop(
        engine, build_query_config(session_id="s_e2e"), tool_use_context=tctx
    ):
        pass

    assert seen == {"parent": "sess_e2e", "agent": "agent_e2e01"}


# ---------------------------------------------------------------------------
# parent_session_id 链路
# ---------------------------------------------------------------------------


def test_create_context_carries_parent_session_id():
    """工厂从父上下文取 session_id，同时写入子上下文与克隆执行上下文。"""
    parent = ToolUseContext(session_id="sess_origin")
    ctx = create_subagent_context(
        parent_context=parent, agent_def=_agent_def(), main_loop_model="m",
        agent_id="agent_p1", prompt="任务",
    )
    assert ctx.parent_session_id == "sess_origin"
    assert ctx.tool_use_context.parent_session_id == "sess_origin"


def test_create_context_without_parent_is_empty():
    ctx = create_subagent_context(
        parent_context=None, agent_def=_agent_def(), main_loop_model="m",
        agent_id="agent_p2", prompt="任务",
    )
    assert ctx.parent_session_id == ""
    assert ctx.tool_use_context.parent_session_id == ""


@pytest.mark.asyncio
async def test_resume_registry_priority_and_evicted_fallback(star):
    """复活补链：注册表记录优先，evicted 后回退调用方上下文；两处字段同步。"""
    from tools.subagent.resume import resume_agent_background

    async def fake_run_agent(ctx, tools, system_prompt):
        yield {"role": "assistant", "content": "ok"}

    star.setattr("tools.subagent.runner.run_agent", fake_run_agent)
    star.setattr(
        "tools.subagent.resume.get_agent_transcript",
        lambda agent_id: [{"role": "user", "content": "历史"}],
    )
    star.setattr(
        "tools.subagent.resume.read_agent_metadata",
        lambda agent_id: {"agent_type": "general-purpose"},
    )

    # 分支 1：注册表有记录 → 优先用记录值（调用方上下文值被覆盖）
    origin_ctx = create_subagent_context(
        parent_context=None, agent_def=_agent_def(), main_loop_model="m",
        agent_id="agent_rp", prompt="原始",
    )
    _test_registry.register("agent_rp", origin_ctx, parent_session_id="sess_origin")

    await resume_agent_background(
        "agent_rp", prompt="继续",
        parent_context=ToolUseContext(session_id="sess_caller"),
    )
    rec = _test_registry.get("agent_rp")
    await rec.task
    resumed = _test_registry.get_ctx("agent_rp")
    assert resumed is not None
    assert resumed.parent_session_id == "sess_origin"
    assert resumed.tool_use_context.parent_session_id == "sess_origin"

    # 分支 2：注册表无记录（evicted）→ 回退调用方上下文
    await resume_agent_background(
        "agent_re", prompt="继续",
        parent_context=ToolUseContext(session_id="sess_caller2"),
    )
    rec2 = _test_registry.get("agent_re")
    await rec2.task
    resumed2 = _test_registry.get_ctx("agent_re")
    assert resumed2 is not None
    assert resumed2.parent_session_id == "sess_caller2"
    assert resumed2.tool_use_context.parent_session_id == "sess_caller2"


# ---------------------------------------------------------------------------
# RespondToCoordinator 上行通道
# ---------------------------------------------------------------------------


def _respond_input(summary="进度汇报", message="第 1 步完成"):
    from tools.subagent.respond_tool import RespondToCoordinatorInput

    return RespondToCoordinatorInput(summary=summary, message=message)


@pytest.mark.asyncio
async def test_respond_tool_enqueues_envelope(star):
    """父活跃形态：信封入通知队列，四要素与回复提示齐备，可被 drain 取走。"""
    from tools.subagent.respond_tool import _execute

    result = await _execute(
        _respond_input(),
        ToolUseContext(tool_use_id="agent_rep1", parent_session_id="sess_p1"),
    )
    assert not result.is_error
    assert "queued" in result.content

    msgs = notify.drain_notifications("sess_p1")
    assert len(msgs) == 1
    body = msgs[0]["content"]
    assert msgs[0]["role"] == "user"
    for fragment in (
        "<subagent-message>", "agent-id: agent_rep1",
        "summary: 进度汇报", "第 1 步完成", "非用户输入", "SendMessage",
    ):
        assert fragment in body


@pytest.mark.asyncio
async def test_respond_tool_triggers_wakeup_hook(star):
    """父空闲形态：推送触发唤起钩子（既有 auto-resume 管线）。"""
    from tools.subagent.respond_tool import _execute

    woken: list[str] = []
    notify.register_wakeup_hook(lambda sid: woken.append(sid))
    try:
        await _execute(
            _respond_input(),
            ToolUseContext(tool_use_id="agent_rep2", parent_session_id="sess_p2"),
        )
    finally:
        notify.register_wakeup_hook(None)  # type: ignore[arg-type]
    assert woken == ["sess_p2"]


@pytest.mark.asyncio
async def test_respond_tool_rejects_without_parent(star):
    """未绑定父会话：明确报错且不产生脏消息。"""
    from tools.subagent.respond_tool import _execute

    result = await _execute(
        _respond_input(),
        ToolUseContext(tool_use_id="agent_rep3", parent_session_id=""),
    )
    assert result.is_error
    assert notify.pending_count("sess_p3") == 0


@pytest.mark.asyncio
async def test_respond_tool_resolves_agent_type(star):
    """agent_type 经注册表反查进信封。"""
    from tools.subagent.respond_tool import _execute

    ctx = create_subagent_context(
        parent_context=ToolUseContext(session_id="sess_p4"),
        agent_def=_agent_def(agent_type="Explore"),
        main_loop_model="m", agent_id="agent_rep4", prompt="任务",
    )
    _test_registry.register("agent_rep4", ctx, parent_session_id="sess_p4")

    await _execute(_respond_input(summary="s", message="m"), ctx.tool_use_context)
    body = notify.drain_notifications("sess_p4")[0]["content"]
    assert "agent-type: Explore" in body


# ---------------------------------------------------------------------------
# 父侧契约文案
# ---------------------------------------------------------------------------


def test_parent_contracts_cover_five_clauses():
    """Agent/SendMessage 说明覆盖五约定语义，转述句无并列重复。"""
    from tools import get_tools

    by_name = {t.name: t for t in get_tools()}
    agent_prompt = by_name["Agent"].prompt
    # 约定 1：必新建 + 约定 2 前半：按 id 续聊（现有句保留）
    assert "A new Agent call always starts a fresh agent" in agent_prompt
    assert "To continue a previously spawned agent" in agent_prompt
    # 约定 4：委派后不重复
    assert "don't also run it yourself" in agent_prompt
    # 约定 5：不读输出文件
    assert "do not read or tail its output file" in agent_prompt
    # 转述语义仅一处
    assert agent_prompt.count("returned only to you") == 1

    sm_prompt = by_name["SendMessage"].prompt
    assert "自动送达" in sm_prompt
    assert "轮次边界" in sm_prompt
    assert "复活" in sm_prompt
    assert "横向通道" in sm_prompt


def test_background_launch_text_has_no_duplicate_hint():
    """后台启动文案含「不要重复该任务正在做的事」与续聊指引。"""
    import pathlib

    import tools.subagent.agent_tool as agent_tool_mod

    source = pathlib.Path(agent_tool_mod.__file__).read_text(encoding="utf-8")
    assert "不要重复该任务正在做的事" in source
    assert "SendMessage 可续聊" in source
