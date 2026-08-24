"""子代理任务管理测试 - REST 端点、模型工具、权限拒绝原因、SendMessage 语义。"""

from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel

from tools.protocol import ToolUseContext, build_tool
from tools.subagent.context import AgentDefinition, create_subagent_context
from tools.subagent.registry import (
    STATUS_COMPLETED,
    STATUS_RUNNING,
    STATUS_STOPPED,
    SubagentTaskRegistry,
)


@pytest.fixture
def registry(monkeypatch):
    """新注册表实例并替换全局单例，隔离测试状态。

    get_subagent_registry() 调用时读取 registry 模块的全局 _subagent_registry，
    故仅替换该全局即可，各消费模块的函数内导入无需单独打桩。
    """
    fresh = SubagentTaskRegistry()
    import tools.subagent.registry as reg_mod
    monkeypatch.setattr(reg_mod, "_subagent_registry", fresh)
    yield fresh


def _make_ctx(agent_id: str, parent_session: str | None = None):
    agent_def = AgentDefinition(agent_type="general-purpose", when_to_use="测试")
    return create_subagent_context(
        parent_context=None,
        agent_def=agent_def,
        main_loop_model="m",
        agent_id=agent_id,
        is_async=True,
        prompt="任务",
    ), agent_def


# ---------------------------------------------------------------------------
# REST 端点
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_detail_output_stop(registry):
    """列表/详情/输出/停止端点行为正确。"""
    from server.routers.subagents import routes

    ctx, _ = _make_ctx("agent_api1", "sess_api")
    registry.register(
        "agent_api1", ctx, agent_type="Explore", parent_session_id="sess_api"
    )

    # 列表（按会话过滤）
    listing = routes.list_subagents(session_id="sess_api")
    assert [s["agent_id"] for s in listing["subagents"]] == ["agent_api1"]
    assert listing["subagents"][0]["status"] == STATUS_RUNNING
    assert routes.list_subagents(session_id="sess_other") == {"subagents": []}

    # 详情
    detail = routes.get_subagent("agent_api1")
    assert detail["agent_type"] == "Explore"
    assert detail["parent_session_id"] == "sess_api"

    # 详情 404
    missing = routes.get_subagent("agent_nope")
    assert missing.status_code == 404

    # 输出：运行中无 final_text，返回中间输出占位
    output = routes.get_subagent_output("agent_api1")
    assert output["status"] == STATUS_RUNNING
    assert "no output yet" in output["output"]

    # 完成后输出最终结果
    registry.set_result("agent_api1", status=STATUS_COMPLETED, final_text="答案")
    output2 = routes.get_subagent_output("agent_api1")
    assert output2["output"] == "答案"

    # 停止已完成任务是幂等的
    stop = await routes.stop_subagent("agent_api1")
    assert stop["ok"] is True
    assert stop["status"] == STATUS_COMPLETED


@pytest.mark.asyncio
async def test_stop_foreground_sets_abort_event(registry):
    """前台任务停止：置位其 abort 事件。"""
    from server.routers.subagents import routes

    agent_def = AgentDefinition(agent_type="general-purpose", when_to_use="测试")
    ctx = create_subagent_context(
        parent_context=None, agent_def=agent_def, main_loop_model="m",
        agent_id="agent_api2", prompt="任务",
        parent_abort_event=asyncio.Event(),
    )
    registry.register("agent_api2", ctx)
    result = await routes.stop_subagent("agent_api2")
    assert result["ok"] is True
    assert result["status"] == "stopping"
    assert ctx.abort_event is not None and ctx.abort_event.is_set()


# ---------------------------------------------------------------------------
# 模型工具
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_subagent_output_tool(registry, monkeypatch):
    """GetSubagentOutput：未知 id 报错，完成返回最终结果。"""
    from tools.subagent.task_tools import (
        GetSubagentOutputInput,
        StopSubagentInput,
        _get_output,
        _stop_subagent,
    )

    missing = await _get_output(GetSubagentOutputInput(agent_id="x"), ToolUseContext())
    assert missing.is_error is True

    ctx, _ = _make_ctx("agent_tool1")
    registry.register("agent_tool1", ctx)
    registry.set_result(
        "agent_tool1",
        status=STATUS_COMPLETED,
        final_text="最终答案",
        usage={"total_tokens": 42, "tool_uses": 3, "duration_ms": 900},
    )
    result = await _get_output(
        GetSubagentOutputInput(agent_id="agent_tool1"), ToolUseContext()
    )
    assert "最终答案" in result.content
    assert result.metadata["source"] == "final"

    # 截断
    truncated = await _get_output(
        GetSubagentOutputInput(agent_id="agent_tool1", max_chars=2), ToolUseContext()
    )
    assert truncated.metadata["truncated"] is True

    # StopSubagent：已完成时幂等返回
    stop = await _stop_subagent(
        StopSubagentInput(agent_id="agent_tool1"), ToolUseContext()
    )
    assert "already finished" in stop.content


@pytest.mark.asyncio
async def test_stop_subagent_tool_background(registry):
    """StopSubagent：后台任务取消 asyncio 句柄。"""
    from tools.subagent.task_tools import StopSubagentInput, _stop_subagent

    ctx, _ = _make_ctx("agent_tool2")
    registry.register("agent_tool2", ctx)

    async def _noop():
        await asyncio.sleep(30)

    async_task = asyncio.create_task(_noop())
    registry.attach_task("agent_tool2", async_task)

    result = await _stop_subagent(
        StopSubagentInput(agent_id="agent_tool2"), ToolUseContext()
    )
    assert result.metadata["status"] == "stopping"
    with pytest.raises(asyncio.CancelledError):
        await async_task
    registry.set_result("agent_tool2", status=STATUS_STOPPED, error="stopped by request")


# ---------------------------------------------------------------------------
# 权限拒绝原因结构化
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_permission_denied_explains_subagent_boundary():
    """子代理内 ask 决策被拒时说明原因与工具边界，不再黑箱。"""
    from tools.executor import execute_tool_call

    class _PermIn(BaseModel):
        cmd: str = "ls"

    async def _exec(_inp, _ctx):
        raise AssertionError("不应被执行")

    bash_like = build_tool(
        name="Bash",
        description="执行命令",
        input_schema=_PermIn,
        execute=_exec,
        prompt="",
    )
    read_like = build_tool(
        name="Read",
        description="读文件",
        input_schema=_PermIn,
        execute=_exec,
        prompt="",
        is_read_only=True,
    )

    async def ask_check(tool, inp, ctx):
        return {"decision": "ask", "reason": "bash 需要确认"}

    # 子代理上下文 + 无弹窗回调
    result = await execute_tool_call(
        tool_call={"id": "c1", "function": {"name": "Bash", "arguments": "{}"}},
        tools=[bash_like, read_like],
        context=ToolUseContext(tool_use_id="agent_perm1"),
        permission_check=ask_check,
    )
    assert result.is_error is True
    assert "cannot ask the user" in result.content
    assert "Bash" in result.content and "Read" in result.content

    # 主循环上下文保持原有简短拒绝文案
    result_main = await execute_tool_call(
        tool_call={"id": "c2", "function": {"name": "Bash", "arguments": "{}"}},
        tools=[bash_like],
        context=ToolUseContext(tool_use_id=""),
        permission_check=ask_check,
    )
    assert "no prompt available" in result_main.content


@pytest.mark.asyncio
async def test_transcript_endpoint_404_for_unknown_agent():
    """transcript 端点对无磁盘记录的子代理返回 404。"""
    from server.routers.subagents import routes

    result = routes.get_subagent_transcript("agent_nope_ts")
    assert result.status_code == 404


# ---------------------------------------------------------------------------
# SendMessage 语义一致（2.5）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_message_running_sync_subagent_queued(registry, monkeypatch):
    """对运行中的同步子代理 SendMessage 走入队路径（此前恒走 resume）。"""
    from tools.subagent import send_message as sm_mod

    agent_def = AgentDefinition(agent_type="general-purpose", when_to_use="测试")
    ctx = create_subagent_context(
        parent_context=None, agent_def=agent_def, main_loop_model="m",
        agent_id="agent_sm1", prompt="任务",
    )
    registry.register("agent_sm1", ctx)

    # resume 被打桩（_execute 函数内从 resume 模块导入，打在源模块上）：
    # 若被调用说明走了错误路径
    import tools.subagent.resume as resume_mod

    async def _fail_resume(*a, **k):
        raise AssertionError("running 子代理不应走 resume 路径")

    monkeypatch.setattr(resume_mod, "resume_agent_background", _fail_resume)

    result = await sm_mod._execute(
        sm_mod.SendMessageInput(to="agent_sm1", summary="s", message="追加指令"),
        ToolUseContext(),
    )
    assert "queued" in result.content
    assert ctx.pending_messages == ["追加指令"]


@pytest.mark.asyncio
async def test_send_message_stopped_subagent_resumes(registry, monkeypatch):
    """对已停止的子代理 SendMessage 走 resume 路径。"""
    from tools.subagent import send_message as sm_mod

    agent_def = AgentDefinition(agent_type="general-purpose", when_to_use="测试")
    ctx = create_subagent_context(
        parent_context=None, agent_def=agent_def, main_loop_model="m",
        agent_id="agent_sm2", prompt="任务",
    )
    registry.register("agent_sm2", ctx)
    registry.set_result("agent_sm2", status=STATUS_STOPPED)

    async def _fake_resume(agent_id, prompt, parent_context=None):
        return f"resumed:{prompt}"

    import tools.subagent.resume as resume_mod
    monkeypatch.setattr(resume_mod, "resume_agent_background", _fake_resume)

    result = await sm_mod._execute(
        sm_mod.SendMessageInput(to="agent_sm2", summary="s", message="继续"),
        ToolUseContext(),
    )
    assert result.content == "resumed:继续"
    assert result.metadata["status"] == "resumed"
