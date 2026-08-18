"""abort 传播测试 - 父会话中断事件传导到前台子代理并写 aborted 状态。"""

from __future__ import annotations

import asyncio

import pytest

from tools.protocol import ToolUseContext
from tools.subagent.context import AgentDefinition, create_subagent_context


def _make_agent_def() -> AgentDefinition:
    return AgentDefinition(agent_type="general-purpose", when_to_use="测试")


def test_sync_context_shares_parent_abort_event():
    """同步子代理共享父会话的中断事件（非复制）。"""
    ev = asyncio.Event()
    ctx = create_subagent_context(
        parent_context=None,
        agent_def=_make_agent_def(),
        main_loop_model="m",
        is_async=False,
        prompt="p",
        parent_abort_event=ev,
    )
    assert ctx.abort_event is ev


def test_async_context_gets_independent_event():
    """异步子代理创建独立中断事件，不受父事件影响。"""
    parent_ev = asyncio.Event()
    ctx = create_subagent_context(
        parent_context=None,
        agent_def=_make_agent_def(),
        main_loop_model="m",
        is_async=True,
        prompt="p",
        parent_abort_event=parent_ev,
    )
    assert ctx.abort_event is not None
    assert ctx.abort_event is not parent_ev


@pytest.fixture
def patched_pipeline(monkeypatch):
    """打桩模型解析与 run_agent，隔离真实引擎循环。"""
    import query.services.api.client as api_client
    monkeypatch.setattr(api_client, "get_default_model", lambda: "test-model")

    import tools.subagent.transcript as transcript
    monkeypatch.setattr(transcript, "record_sidechain_transcript", lambda *a, **k: None)
    monkeypatch.setattr(transcript, "write_agent_metadata", lambda **k: None)

    import tools as tools_pkg
    monkeypatch.setattr(tools_pkg, "get_tools", lambda *a, **k: [])

    yield monkeypatch


@pytest.mark.asyncio
async def test_agent_tool_passes_parent_event_and_records_aborted(
    patched_pipeline,
):
    """父事件置位时，前台子代理优雅退出且注册表记 aborted。"""
    from tools.subagent import agent_tool
    from tools.subagent.registry import STATUS_ABORTED, get_subagent_registry

    parent_ev = asyncio.Event()
    parent_ev.set()
    context = ToolUseContext(tool_use_id="", abort_controller=parent_ev)

    async def fake_run_agent(ctx, tools, system_prompt):
        # 断言子代理拿到的是父事件
        assert ctx.abort_event is parent_ev
        yield {"role": "assistant", "content": "[Subagent aborted]"}

    patched_pipeline.setattr(
        "tools.subagent.runner.run_agent", fake_run_agent
    )

    result = await agent_tool._execute(
        agent_tool.AgentInput(description="测试", prompt="任务"), context
    )
    assert "[Subagent aborted]" in result.content

    task = get_subagent_registry().get(result.metadata["agent_id"])
    assert task is not None
    assert task.status == STATUS_ABORTED


@pytest.mark.asyncio
async def test_agent_tool_result_includes_usage_tail(patched_pipeline):
    """前台 tool_result 尾部含 usage 统计与 agentId 续聊指引。"""
    from tools.subagent import agent_tool

    context = ToolUseContext(tool_use_id="")

    async def fake_run_agent(ctx, tools, system_prompt):
        # 模拟 runner 的 finally 汇总
        ctx.usage = {"total_tokens": 77, "tool_uses": 5, "duration_ms": 1200}
        yield {"role": "assistant", "content": "完成了"}

    patched_pipeline.setattr("tools.subagent.runner.run_agent", fake_run_agent)

    result = await agent_tool._execute(
        agent_tool.AgentInput(description="测试", prompt="任务"), context
    )
    assert "完成了" in result.content
    assert "tokens: 77" in result.content
    assert "tool_uses: 5" in result.content
    assert "duration_ms: 1200" in result.content
    assert result.metadata["agent_id"] in result.content
    assert "SendMessage" in result.content
    assert result.metadata["usage"]["total_tokens"] == 77


@pytest.mark.asyncio
async def test_agent_tool_cancel_records_aborted(patched_pipeline):
    """父任务 cancel（CancelledError）传导时，注册表记 aborted 且异常继续上抛。"""
    from tools.subagent import agent_tool
    from tools.subagent.registry import STATUS_ABORTED, get_subagent_registry

    parent_ev = asyncio.Event()
    context = ToolUseContext(tool_use_id="", abort_controller=parent_ev)

    async def fake_run_agent(ctx, tools, system_prompt):
        raise asyncio.CancelledError()
        yield  # pragma: no cover

    patched_pipeline.setattr(
        "tools.subagent.runner.run_agent", fake_run_agent
    )

    with pytest.raises(asyncio.CancelledError):
        await agent_tool._execute(
            agent_tool.AgentInput(description="测试", prompt="任务"), context
        )

    # 注册表里最新的前台任务状态为 aborted
    reg = get_subagent_registry()
    tasks = [t for t in reg.list_tasks() if t.agent_type == "general-purpose"]
    assert tasks[-1].status == STATUS_ABORTED
