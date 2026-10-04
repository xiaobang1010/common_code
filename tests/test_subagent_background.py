"""真后台运行测试 - 启动、完成通知、异常处理、停止、并发限制。"""

from __future__ import annotations

import asyncio

import pytest

from tools.protocol import ToolUseContext
from tools.subagent.context import AgentDefinition, create_subagent_context
from tools.subagent.registry import (
    MODE_BACKGROUND,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_RUNNING,
    STATUS_STOPPED,
    SubagentTaskRegistry,
    get_subagent_registry,
)
from tools.subagent import notify


@pytest.fixture
def clean_registry(monkeypatch):
    """给注册表换新实例并打桩 transcript 落盘，隔离测试间状态。"""
    # 生命周期引擎经模块间接访问注册表，打在 registry 模块上即全链路生效
    monkeypatch.setattr(
        "tools.subagent.registry.get_subagent_registry", lambda: _test_registry
    )

    import tools.subagent.transcript as transcript
    monkeypatch.setattr(transcript, "record_sidechain_transcript", lambda *a, **k: None)
    monkeypatch.setattr(transcript, "write_agent_metadata", lambda **k: None)
    monkeypatch.setattr(transcript, "save_full_result", lambda *a, **k: "/tmp/fake.txt")
    monkeypatch.setattr(transcript, "append_task_output", lambda *a, **k: None)
    yield _test_registry


_test_registry = SubagentTaskRegistry()


def _make_ctx(agent_id: str) -> object:
    agent_def = AgentDefinition(agent_type="general-purpose", when_to_use="测试")
    return create_subagent_context(
        parent_context=None,
        agent_def=agent_def,
        main_loop_model="m",
        agent_id=agent_id,
        is_async=True,
        prompt="任务",
    )


@pytest.mark.asyncio
async def test_background_runs_and_notifies(clean_registry, monkeypatch):
    """后台子代理真实运行，完成后注册表记 completed 并投递通知。"""
    from tools.subagent import background

    async def fake_run_agent(ctx, tools, system_prompt):
        yield {"role": "assistant", "content": "后台结果"}

    monkeypatch.setattr("tools.subagent.runner.run_agent", fake_run_agent)

    ctx = _make_ctx("agent_bg1")
    task = background.launch_background_subagent(
        ctx, [], "sys", description="后台任务", parent_session_id="sess_bg"
    )
    assert task.status == STATUS_RUNNING
    assert task.mode == MODE_BACKGROUND
    # 注册表持有 asyncio 任务引用
    assert task.task is not None

    await task.task

    record = clean_registry.get("agent_bg1")
    assert record.status == STATUS_COMPLETED
    assert record.final_text == "后台结果"
    assert record.usage["duration_ms"] >= 0

    # 通知进入父会话队列（含最小 usage 字段与续聊指引）
    notices = notify.drain_notifications("sess_bg")
    assert len(notices) == 1
    body = notices[0]["content"]
    assert "agent_bg1" in body
    assert "completed" in body
    assert "SendMessage" in body


@pytest.mark.asyncio
async def test_background_exception_recorded(clean_registry, monkeypatch):
    """后台异常不外泄，注册表记 failed 并带原因，通知照投。"""
    from tools.subagent import background

    async def fake_run_agent(ctx, tools, system_prompt):
        raise RuntimeError("engine exploded")
        yield  # pragma: no cover

    monkeypatch.setattr("tools.subagent.runner.run_agent", fake_run_agent)

    ctx = _make_ctx("agent_bg2")
    task = background.launch_background_subagent(
        ctx, [], "sys", parent_session_id="sess_bg2"
    )
    await task.task  # 不应抛异常（统一 handler 接住）

    record = clean_registry.get("agent_bg2")
    assert record.status == STATUS_FAILED
    assert "engine exploded" in record.error
    assert notify.pending_count("sess_bg2") == 1


@pytest.mark.asyncio
async def test_background_stop_records_stopped(clean_registry, monkeypatch):
    """取消后台任务（stop 语义）记 stopped 并通知。"""
    from tools.subagent import background

    started = asyncio.Event()

    async def fake_run_agent(ctx, tools, system_prompt):
        started.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            raise
        yield  # pragma: no cover

    monkeypatch.setattr("tools.subagent.runner.run_agent", fake_run_agent)

    ctx = _make_ctx("agent_bg3")
    task = background.launch_background_subagent(
        ctx, [], "sys", parent_session_id="sess_bg3"
    )
    await started.wait()
    # 走统一停止入口（记录停止原因后取消驱动任务）
    from tools.subagent.lifecycle import stop_subagent

    assert stop_subagent("agent_bg3") == "stopping"
    # 驱动任务内部捕获 CancelledError 并记 stopped，不向外抛
    await task.task

    record = clean_registry.get("agent_bg3")
    assert record.status == STATUS_STOPPED
    assert notify.pending_count("sess_bg3") == 1


def test_concurrency_limits(clean_registry, monkeypatch):
    """会话级与全局并发超限返回明确错误。"""
    from tools.subagent import agent_tool

    # 预置 4 个 running 任务同属 sess_lim
    for i in range(agent_tool.MAX_SUBAGENTS_PER_SESSION):
        agent_id = f"agent_lim{i}"
        clean_registry.register(
            agent_id, _make_ctx(agent_id), parent_session_id="sess_lim"
        )

    ctx = ToolUseContext(session_id="sess_lim")
    err = agent_tool._check_concurrency(ctx)
    assert err is not None
    assert f"limit: {agent_tool.MAX_SUBAGENTS_PER_SESSION}" in err

    # 其他会话不受影响
    assert agent_tool._check_concurrency(ToolUseContext(session_id="sess_other")) is None

    # 全局超限：填满剩余名额
    for i in range(
        agent_tool.MAX_SUBAGENTS_GLOBAL - agent_tool.MAX_SUBAGENTS_PER_SESSION
    ):
        agent_id = f"agent_glb{i}"
        clean_registry.register(agent_id, _make_ctx(agent_id))
    assert agent_tool._check_concurrency(ToolUseContext(session_id="sess_other")) is not None
    assert "global" in agent_tool._check_concurrency(ToolUseContext(session_id="sess_other"))
