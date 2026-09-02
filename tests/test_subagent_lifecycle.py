"""执行底座生命周期测试 - 提升竞态、看门狗、中止即时链、预算、会话化、投递。"""

from __future__ import annotations

import asyncio
import time

import pytest

from startup.config.types import SubagentsConfig
from tools.protocol import ToolUseContext
from tools.subagent import notify
from tools.subagent.context import AgentDefinition, create_subagent_context
from tools.subagent.registry import (
    MODE_BACKGROUND,
    STATUS_ABORTED,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_STOPPED,
    SubagentTaskRegistry,
)

_test_registry = SubagentTaskRegistry()


@pytest.fixture
def substrate(monkeypatch, tmp_path):
    """统一桩：新注册表 + 配置段 + 转录打桩 + 工作区指向临时目录。"""
    monkeypatch.setattr(
        "tools.subagent.registry.get_subagent_registry", lambda: _test_registry
    )
    # 每个测试前清空注册表与通知队列
    _test_registry._tasks.clear()

    # 桩返回完整 GlobalConfig（生命周期经 .subagents 取段）
    from startup.config import GlobalConfig

    def _cfg() -> GlobalConfig:
        return GlobalConfig(subagents=SubagentsConfig())

    monkeypatch.setattr("startup.config.get_global_config", lambda: _cfg())
    monkeypatch.setattr("query.services.api.client.get_default_model", lambda: "m")
    import tools as tools_pkg

    monkeypatch.setattr(tools_pkg, "get_tools", lambda *a, **k: [])
    monkeypatch.setattr("server.paths.effective_root", lambda: str(tmp_path))

    import tools.subagent.transcript as transcript

    monkeypatch.setattr(transcript, "record_sidechain_transcript", lambda *a, **k: None)
    monkeypatch.setattr(transcript, "write_agent_metadata", lambda **k: None)
    monkeypatch.setattr(transcript, "save_full_result", lambda *a, **k: "/tmp/fake.txt")
    monkeypatch.setattr(transcript, "append_task_output", lambda *a, **k: None)
    return monkeypatch


def _set_config(monkeypatch, **kwargs) -> None:
    """覆盖测试用的 subagents 配置段。"""
    from startup.config import GlobalConfig

    cfg = GlobalConfig(subagents=SubagentsConfig(**kwargs))
    monkeypatch.setattr("startup.config.get_global_config", lambda: cfg)


def _agent_def(**kw) -> AgentDefinition:
    defaults = dict(agent_type="general-purpose", when_to_use="测试")
    defaults.update(kw)
    return AgentDefinition(**defaults)


def _spawn_request(prompt="任务", description="测试任务", run_in_background=False, abort_event=None):
    from tools.subagent.lifecycle import SpawnRequest

    parent = ToolUseContext(session_id="sess_parent", abort_controller=abort_event)
    return SpawnRequest(
        agent_def=_agent_def(),
        prompt=prompt,
        description=description,
        parent_context=parent,
        run_in_background=run_in_background,
    )


# ---------------------------------------------------------------------------
# 8.1 提升竞态
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fast_completion_beats_promotion(substrate):
    """快完成先于提升定时器：前台正常返回 completed。"""
    _set_config(substrate, auto_background_ms=5000)

    async def fake_run_agent(ctx, tools, system_prompt):
        yield {"role": "assistant", "content": "做完了"}

    substrate.setattr("tools.subagent.runner.run_agent", fake_run_agent)

    from tools.subagent.lifecycle import spawn_subagent

    result = await spawn_subagent(_spawn_request())
    assert result.kind == "completed"
    assert result.outcome.status == STATUS_COMPLETED
    record = _test_registry.get(result.agent_id)
    assert record.promoted is False


@pytest.mark.asyncio
async def test_promotion_timer_promotes_foreground(substrate):
    """提升定时器先到：立即返回 async_launched，任务转后台并通知。"""
    _set_config(substrate, auto_background_ms=30)
    notify.drain_notifications("sess_parent")  # 清场

    gate = asyncio.Event()

    async def fake_run_agent(ctx, tools, system_prompt):
        yield {"role": "assistant", "content": "阶段一"}
        await gate.wait()  # 模拟长任务，直到测试放行
        yield {"role": "assistant", "content": "最终结果"}

    substrate.setattr("tools.subagent.runner.run_agent", fake_run_agent)

    from tools.subagent.lifecycle import spawn_subagent

    started = time.monotonic()
    result = await spawn_subagent(_spawn_request())
    elapsed = time.monotonic() - started

    assert result.kind == "async_launched"
    assert elapsed < 2.0  # 主轮次被立即释放，没有等长任务
    record = _test_registry.get(result.agent_id)
    assert record.promoted is True
    assert record.mode == MODE_BACKGROUND

    # promoted 通知已投递
    notices = notify.drain_notifications("sess_parent")
    assert any("promoted" in n["content"] for n in notices)

    # 放行后后台跑完，completed 通知随之到达
    gate.set()
    await record.task
    record = _test_registry.get(result.agent_id)
    assert record.status == STATUS_COMPLETED
    notices = notify.drain_notifications("sess_parent")
    assert any("completed" in n["content"] for n in notices)


@pytest.mark.asyncio
async def test_promotion_detaches_parent_abort(substrate):
    """提升后与父中止解绑：父会话 abort 不影响已提升代理。"""
    _set_config(substrate, auto_background_ms=30)
    notify.drain_notifications("sess_parent")

    gate = asyncio.Event()

    async def fake_run_agent(ctx, tools, system_prompt):
        await gate.wait()
        yield {"role": "assistant", "content": "提升后完成"}

    substrate.setattr("tools.subagent.runner.run_agent", fake_run_agent)

    from tools.subagent.lifecycle import spawn_subagent

    parent_ev = asyncio.Event()
    result = await spawn_subagent(_spawn_request(abort_event=parent_ev))
    assert result.kind == "async_launched"

    # 父会话中止：已提升代理不受影响
    parent_ev.set()
    await asyncio.sleep(0.05)
    gate.set()
    record = _test_registry.get(result.agent_id)
    await record.task
    record = _test_registry.get(result.agent_id)
    assert record.status == STATUS_COMPLETED  # 不是 aborted


# ---------------------------------------------------------------------------
# 8.1 活性看门狗
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_watchdog_stops_inactive(substrate):
    """超过活性超时无活动：看门狗中止任务，终态 stopped。"""
    _set_config(substrate, inactivity_timeout_ms=50, auto_background_ms=0)

    started_ev = asyncio.Event()

    async def fake_run_agent(ctx, tools, system_prompt):
        yield {"role": "assistant", "content": "开始"}
        started_ev.set()
        await asyncio.sleep(10)  # 长时间无活动
        yield {"role": "assistant", "content": "不应到达"}

    substrate.setattr("tools.subagent.runner.run_agent", fake_run_agent)

    from tools.subagent.lifecycle import spawn_subagent

    t0 = time.monotonic()
    result = await spawn_subagent(_spawn_request())
    elapsed = time.monotonic() - t0

    assert result.kind == "completed"  # 前台等待到终态（终态本身是 stopped）
    assert result.outcome.status == STATUS_STOPPED
    assert "inactivity" in (result.outcome.error or "")
    assert elapsed < 5.0  # 远早于 10s 的自然时长
    record = _test_registry.get(result.agent_id)
    assert record.status == STATUS_STOPPED


@pytest.mark.asyncio
async def test_watchdog_activity_keeps_alive(substrate):
    """持续有活动：看门狗不误杀，任务正常完成。"""
    _set_config(substrate, inactivity_timeout_ms=80, auto_background_ms=0)

    async def fake_run_agent(ctx, tools, system_prompt):
        for i in range(5):
            await asyncio.sleep(0.03)  # 每次睡眠都短于超时，且产出事件重置计时
            if ctx.on_activity is not None:
                ctx.on_activity()  # 模拟真实 runner 的活动上报
            yield {"role": "assistant", "content": f"第{i}步"}

    substrate.setattr("tools.subagent.runner.run_agent", fake_run_agent)

    from tools.subagent.lifecycle import spawn_subagent

    result = await spawn_subagent(_spawn_request())
    assert result.outcome.status == STATUS_COMPLETED


# ---------------------------------------------------------------------------
# 8.1 中止即时链
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parent_abort_cancels_driver_immediately(substrate):
    """父中止置位后驱动任务被立即取消（不等自然跑完）。"""
    _set_config(substrate, auto_background_ms=0, inactivity_timeout_ms=0)

    async def fake_run_agent(ctx, tools, system_prompt):
        for i in range(100):
            await asyncio.sleep(0.05)  # 自然时长 5s
            yield {"role": "assistant", "content": f"慢任务 {i}"}

    substrate.setattr("tools.subagent.runner.run_agent", fake_run_agent)

    from tools.subagent.lifecycle import spawn_subagent

    parent_ev = asyncio.Event()

    async def _abort_soon():
        await asyncio.sleep(0.15)
        parent_ev.set()

    aborter = asyncio.get_event_loop().create_task(_abort_soon())
    t0 = time.monotonic()
    result = await spawn_subagent(_spawn_request(abort_event=parent_ev))
    elapsed = time.monotonic() - t0
    await aborter

    assert elapsed < 2.0  # 秒级中止，远早于 5s 自然时长
    assert result.outcome.status == STATUS_ABORTED
    record = _test_registry.get(result.agent_id)
    assert record.status == STATUS_ABORTED


@pytest.mark.asyncio
async def test_stop_subagent_unified_entry(substrate):
    """统一停止入口：后台任务经驱动任务取消记 stopped。"""
    _set_config(substrate)

    started_ev = asyncio.Event()

    async def fake_run_agent(ctx, tools, system_prompt):
        started_ev.set()
        await asyncio.sleep(30)
        yield {"role": "assistant", "content": "不应到达"}

    substrate.setattr("tools.subagent.runner.run_agent", fake_run_agent)

    from tools.subagent.lifecycle import spawn_subagent, stop_subagent

    result = await spawn_subagent(_spawn_request(run_in_background=True))
    await started_ev.wait()
    assert stop_subagent(result.agent_id) == "stopping"
    record = _test_registry.get(result.agent_id)
    await record.task
    record = _test_registry.get(result.agent_id)
    assert record.status == STATUS_STOPPED
    assert stop_subagent(result.agent_id) == "already_finished"
    assert stop_subagent("agent_nonexistent") == "not_found"


# ---------------------------------------------------------------------------
# 8.2 预算护栏
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_token_budget_stops_loop():
    """token 预算超限：轮首检查拦截，优雅停止并说明原因。"""
    from dataclasses import dataclass

    from query.config import build_query_config
    from query.engine import QueryEngine, build_engine_config
    from query.loop import query_loop
    from query.services.api.llm import StreamEvent

    @dataclass
    class FakeDeps:
        calls: int = 0

        def get_uuid(self) -> str:
            return "u"

        async def call_model(self, messages, tools, model, max_tokens, temperature):
            self.calls += 1
            yield StreamEvent(type="content", content=f"输出{self.calls}")
            if self.calls <= 2:
                # 前两轮发起工具调用（工具不存在，执行器返回错误结果，循环继续）
                yield StreamEvent(
                    type="tool_call_delta", tool_call_index=0,
                    tool_call_id=f"call_{self.calls}", tool_call_name="NoSuchTool",
                )
                yield StreamEvent(
                    type="tool_call_delta", tool_call_index=0, tool_call_arguments="{}",
                )
                yield StreamEvent(type="done", finish_reason="tool_calls")
            else:
                yield StreamEvent(type="done", finish_reason="stop")
            yield StreamEvent(type="usage", usage={"total_tokens": 100})

    deps = FakeDeps()
    config = build_engine_config(
        model="fake",
        tools=[],
        max_turns=10,
        token_budget=150,  # 第二轮结束累计 200，第三轮轮首被拦截
        deps=deps,  # type: ignore[arg-type]
    )
    engine = QueryEngine(config, initial_messages=[{"role": "user", "content": "t"}])
    events = [ev async for ev in query_loop(engine, build_query_config(session_id="s"))]

    # 模型只被调用两轮，第三轮在轮首被预算拦截
    assert deps.calls == 2
    assert engine.total_usage >= 150
    budget_messages = [
        e for e in events
        if isinstance(e, dict) and e.get("role") == "assistant"
        and "token 预算" in str(e.get("content", ""))
    ]
    assert len(budget_messages) == 1


@pytest.mark.asyncio
async def test_no_budget_main_conversation_unaffected():
    """未配置预算：循环不受预算检查影响（正常完成）。"""
    from dataclasses import dataclass

    from query.config import build_query_config
    from query.engine import QueryEngine, build_engine_config
    from query.loop import query_loop
    from query.services.api.llm import StreamEvent

    @dataclass
    class FakeDeps:
        calls: int = 0

        def get_uuid(self) -> str:
            return "u"

        async def call_model(self, messages, tools, model, max_tokens, temperature):
            self.calls += 1
            yield StreamEvent(type="content", content="完成")
            yield StreamEvent(type="done", finish_reason="stop")
            yield StreamEvent(type="usage", usage={"total_tokens": 100})

    deps = FakeDeps()
    config = build_engine_config(
        model="fake", tools=[], token_budget=None, deps=deps,  # type: ignore[arg-type]
    )
    engine = QueryEngine(config, initial_messages=[{"role": "user", "content": "t"}])
    events = [ev async for ev in query_loop(engine, build_query_config(session_id="s"))]
    assert deps.calls == 1
    assert not any(
        isinstance(e, dict) and "token 预算" in str(e.get("content", "")) for e in events
    )


def test_apply_budget_defaults(substrate):
    """预算默认值：未指定轮次/预算时应用全局默认。"""
    _set_config(substrate, max_turns_default=40, token_budget_default=200000)
    from tools.subagent.lifecycle import _apply_budget_defaults

    ctx = create_subagent_context(
        parent_context=None,
        agent_def=_agent_def(),
        main_loop_model="m",
        prompt="t",
    )
    assert ctx.max_turns is None and ctx.token_budget is None
    _apply_budget_defaults(ctx, ctx.agent_def)
    assert ctx.max_turns == 40
    assert ctx.token_budget == 200000

    # profile 显式指定时不被默认覆盖
    ctx2 = create_subagent_context(
        parent_context=None,
        agent_def=_agent_def(max_turns=10, token_budget=500),
        main_loop_model="m",
        prompt="t",
    )
    ctx2.max_turns = None  # 模拟派生路径未透传时的默认回填判断
    _apply_budget_defaults(ctx2, ctx2.agent_def)
    assert ctx2.max_turns == 40  # profile 未指定轮次 → 默认
    assert ctx2.token_budget == 500  # profile 指定预算 → 保留


# ---------------------------------------------------------------------------
# 8.3 会话化
# ---------------------------------------------------------------------------


def _make_store(tmp_path):
    from session.store import SessionStore

    return SessionStore(db_path=tmp_path / "sessions.db")


@pytest.mark.asyncio
async def test_spawn_creates_child_session_and_syncs(substrate, tmp_path):
    """派生即建子会话：字段齐全、消息落库、终态同步。"""
    _set_config(substrate, auto_background_ms=0)
    store = _make_store(tmp_path)
    import server.state

    substrate.setattr(server.state, "session_store", store)

    # 用真 runner（才会走每轮落库），循环本体打桩
    # 模拟真循环行为：assistant 消息先写回引擎历史再 yield
    async def fake_query_loop(engine, config, user_context=None, system_context=None, tool_use_context=None):
        msg = {"role": "assistant", "content": "子会话结果"}
        engine.mutable_messages.append(msg)
        yield msg

    substrate.setattr("query.loop.query_loop", fake_query_loop)

    from tools.subagent.lifecycle import spawn_subagent

    result = await spawn_subagent(_spawn_request(description="会话化任务"))
    assert result.kind == "completed"

    child_id = f"subagent_{result.agent_id}"
    session = store.get_session(child_id)
    assert session is not None
    assert session.origin == "subagent"
    assert session.parent_session_id == "sess_parent"
    # 消息已落库（含初始 user prompt 与 assistant 结果）
    roles = [m.get("role") for m in session.messages]
    assert "user" in roles and "assistant" in roles
    # agent_meta 终态同步
    meta = session.agent_meta
    assert meta["status"] == "completed"
    assert meta["agent_type"] == "general-purpose"
    assert "updated_at" in meta

    # 主会话列表不含子会话
    assert all(s.id != child_id for s in store.list_sessions(str(tmp_path)))


@pytest.mark.asyncio
async def test_child_session_binding_failure_degrades(substrate, tmp_path):
    """子会话存储故障：降级为无子会话模式，子代理正常运行。"""
    _set_config(substrate, auto_background_ms=0)

    class BrokenStore:
        def session_exists(self, sid):
            raise RuntimeError("db down")

    import server.state

    substrate.setattr(server.state, "session_store", BrokenStore())

    async def fake_run_agent(ctx, tools, system_prompt):
        yield {"role": "assistant", "content": "降级运行"}

    substrate.setattr("tools.subagent.runner.run_agent", fake_run_agent)

    from tools.subagent.lifecycle import spawn_subagent

    result = await spawn_subagent(_spawn_request())
    assert result.kind == "completed"
    assert result.outcome.status == STATUS_COMPLETED


@pytest.mark.asyncio
async def test_resume_reuses_child_session(substrate, tmp_path):
    """resume 重入同一 agent_id：复用子会话行，不产生重复。"""
    store = _make_store(tmp_path)
    import server.state

    substrate.setattr(server.state, "session_store", store)

    from tools.subagent.session_binding import ensure_child_session

    sid1 = ensure_child_session(
        "agent_r1",
        parent_session_id="sess_p",
        workspace_path=str(tmp_path),
        title="首次",
        agent_type="general-purpose",
        mode="background",
    )
    sid2 = ensure_child_session(
        "agent_r1",
        parent_session_id="sess_p",
        workspace_path=str(tmp_path),
        title="重入",
        agent_type="general-purpose",
        mode="background",
    )
    assert sid1 == sid2 == "subagent_agent_r1"
    kids = store.list_child_sessions("sess_p")
    assert len(kids) == 1


# ---------------------------------------------------------------------------
# 8.4 投递与终止守卫
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_message_delivered_ack(substrate):
    """运行中投递：队列被消费后返回 delivered。"""
    agent_def = _agent_def()
    ctx = create_subagent_context(
        parent_context=None, agent_def=agent_def, main_loop_model="m",
        agent_id="agent_dlv", prompt="t",
    )
    _test_registry.register("agent_dlv", ctx, parent_session_id="sess_p")

    async def _consumer():
        await asyncio.sleep(0.1)
        ctx.pending_messages.clear()  # 模拟 runner 注入消费

    consumer = asyncio.get_event_loop().create_task(_consumer())

    from tools.subagent.send_message import _execute, SendMessageInput

    result = await _execute(
        SendMessageInput(to="agent_dlv", summary="s", message="追加"),
        ToolUseContext(),
    )
    await consumer
    assert result.metadata["delivery"] == "delivered"


@pytest.mark.asyncio
async def test_send_message_queued_without_consumer(substrate):
    """运行中投递：无消费者时返回 queued，消息留在队列等待注入。"""
    agent_def = _agent_def()
    ctx = create_subagent_context(
        parent_context=None, agent_def=agent_def, main_loop_model="m",
        agent_id="agent_q", prompt="t",
    )
    _test_registry.register("agent_q", ctx, parent_session_id="sess_p")

    # 缩短确认窗口，避免测试等待过久
    import tools.subagent.send_message as sm

    substrate.setattr(sm, "DELIVERY_ACK_WINDOW_S", 0.2)

    result = await sm._execute(
        sm.SendMessageInput(to="agent_q", summary="s", message="排队消息"),
        ToolUseContext(),
    )
    assert result.metadata["delivery"] == "queued"
    assert ctx.pending_messages == ["排队消息"]


@pytest.mark.asyncio
async def test_runner_injection_and_termination_guard(substrate):
    """投递不依赖工具轮次 + 循环终止守卫：队列非空时续跑不丢消息。"""
    from tools.subagent.runner import run_agent

    agent_def = _agent_def()
    ctx = create_subagent_context(
        parent_context=None, agent_def=agent_def, main_loop_model="m",
        agent_id="agent_inj", prompt="初始任务",
    )

    loop_calls = {"count": 0}

    async def fake_query_loop(engine, config, user_context=None, system_context=None, tool_use_context=None):
        loop_calls["count"] += 1
        if loop_calls["count"] == 1:
            yield {"role": "assistant", "content": "回合1"}
            # 模拟 SendMessage 在安全点已过后入队：只能靠终止守卫兜住
            ctx.pending_messages.append("中途指令")
        else:
            yield {"role": "assistant", "content": "回合2"}

    # run_agent 内为函数级导入，桩打在源模块上
    substrate.setattr("query.loop.query_loop", fake_query_loop)

    events = []
    async for msg in run_agent(ctx=ctx, tools=[], system_prompt="sys"):
        events.append(msg)

    user_msgs = [m for m in events if m.get("role") == "user"]
    assistant_msgs = [m for m in events if m.get("role") == "assistant"]
    # 中途指令由终止守卫注入（安全点已过），循环因此续跑一轮
    assert any(m.get("content") == "中途指令" for m in user_msgs)
    assert loop_calls["count"] == 2
    assert len(assistant_msgs) == 2


@pytest.mark.asyncio
async def test_resume_records_actual_terminal_status(substrate):
    """resume 后台运行：异常时按实际结果记 failed，不再无条件 completed。"""
    _set_config(substrate)
    notify.drain_notifications("sess_resume")

    async def fake_run_agent(ctx, tools, system_prompt):
        raise RuntimeError("恢复后炸了")
        yield  # pragma: no cover

    substrate.setattr("tools.subagent.runner.run_agent", fake_run_agent)

    # 准备转录与元数据（桩打在 resume 模块命名空间，其顶层导入已绑定引用）
    substrate.setattr(
        "tools.subagent.resume.get_agent_transcript",
        lambda agent_id: [{"role": "user", "content": "历史"}],
    )
    substrate.setattr(
        "tools.subagent.resume.read_agent_metadata",
        lambda agent_id: {"agent_type": "general-purpose"},
    )

    from tools.subagent.resume import resume_agent_background

    await resume_agent_background(
        "agent_rs", prompt="继续", parent_context=ToolUseContext(session_id="sess_resume"),
    )
    record = _test_registry.get("agent_rs")
    await record.task
    record = _test_registry.get("agent_rs")
    assert record.status == STATUS_FAILED
    assert "恢复后炸了" in (record.error or "")
