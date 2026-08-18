"""SubagentTaskRegistry 单元测试 - 状态流转、消息投递、并发计数。"""

from __future__ import annotations

import asyncio

from tools.subagent.context import AgentDefinition, create_subagent_context
from tools.subagent.registry import (
    MODE_BACKGROUND,
    MODE_FOREGROUND,
    STATUS_ABORTED,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_RUNNING,
    STATUS_STOPPED,
    SubagentTaskRegistry,
)


def _make_ctx(agent_id: str):
    """构造一个最小子代理上下文。"""
    agent_def = AgentDefinition(
        agent_type="general-purpose",
        when_to_use="测试",
    )
    return create_subagent_context(
        parent_context=None,
        agent_def=agent_def,
        main_loop_model="test-model",
        agent_id=agent_id,
        prompt="hello",
    )


def test_register_defaults_to_running():
    """注册后状态为 running，mode 与 agent_type 记录正确。"""
    reg = SubagentTaskRegistry()
    ctx = _make_ctx("agent_t1")
    reg.register("agent_t1", ctx, agent_type="Explore", mode=MODE_FOREGROUND)
    task = reg.get("agent_t1")
    assert task is not None
    assert task.status == STATUS_RUNNING
    assert task.mode == MODE_FOREGROUND
    assert task.agent_type == "Explore"
    assert reg.get_status("agent_t1") == STATUS_RUNNING
    assert reg.get_ctx("agent_t1") is ctx
    assert reg.running_count() == 1


def test_status_transitions():
    """running -> completed/failed/aborted/stopped 全流转可查，终态不再计入运行数。"""
    reg = SubagentTaskRegistry()
    for i, final_status in enumerate(
        [STATUS_COMPLETED, STATUS_FAILED, STATUS_ABORTED, STATUS_STOPPED]
    ):
        agent_id = f"agent_t2_{i}"
        reg.register(agent_id, _make_ctx(agent_id), mode=MODE_BACKGROUND)
        if final_status == STATUS_FAILED:
            reg.set_result(agent_id, status=final_status, error="boom")
        else:
            reg.set_result(agent_id, status=final_status, final_text="done")
        assert reg.get_status(agent_id) == final_status
    assert reg.running_count() == 0
    # 终态任务默认仍保留在列表中
    assert len(reg.list_tasks()) == 4
    assert len(reg.list_tasks(include_terminal=False)) == 0


def test_set_result_updates_usage_and_output():
    """set_result 合并 usage 字段并记录落盘路径。"""
    reg = SubagentTaskRegistry()
    reg.register("agent_t3", _make_ctx("agent_t3"))
    reg.set_result(
        "agent_t3",
        status=STATUS_COMPLETED,
        final_text="答案",
        usage={"total_tokens": 123, "tool_uses": 4, "duration_ms": 5600},
        output_file="/tmp/result.txt",
    )
    task = reg.get("agent_t3")
    assert task.usage == {"total_tokens": 123, "tool_uses": 4, "duration_ms": 5600}
    assert task.output_file == "/tmp/result.txt"
    assert task.final_text == "答案"


def test_queue_pending_message_only_when_running():
    """运行中可入队；终态或未知 agent_id 拒绝入队。"""
    reg = SubagentTaskRegistry()
    ctx = _make_ctx("agent_t4")
    reg.register("agent_t4", ctx)
    assert reg.queue_pending_message("agent_t4", "msg1") is True
    assert ctx.pending_messages == ["msg1"]

    reg.set_result("agent_t4", status=STATUS_COMPLETED, final_text="x")
    assert reg.queue_pending_message("agent_t4", "msg2") is False
    assert ctx.pending_messages == ["msg1"]

    assert reg.queue_pending_message("agent_unknown", "msg") is False


def test_list_tasks_filters_by_session():
    """按父会话过滤任务列表。"""
    reg = SubagentTaskRegistry()
    reg.register("agent_t5", _make_ctx("agent_t5"), parent_session_id="sess_a")
    reg.register("agent_t6", _make_ctx("agent_t6"), parent_session_id="sess_b")
    assert [t.agent_id for t in reg.list_tasks(session_id="sess_a")] == ["agent_t5"]
    assert reg.running_count(session_id="sess_a") == 1


def test_attach_task_holds_reference():
    """attach_task 持有 asyncio 任务引用。"""
    reg = SubagentTaskRegistry()

    async def _noop():
        await asyncio.sleep(0)

    async def main():
        reg.register("agent_t7", _make_ctx("agent_t7"))
        t = asyncio.create_task(_noop())
        reg.attach_task("agent_t7", t)
        await t
        assert reg.get("agent_t7").task is t

    asyncio.run(main())


def test_to_dict_excludes_runtime_objects():
    """序列化不携带 ctx/task 等运行期对象。"""
    reg = SubagentTaskRegistry()
    reg.register("agent_t8", _make_ctx("agent_t8"))
    d = reg.get("agent_t8").to_dict()
    assert "ctx" not in d and "task" not in d
    assert d["agent_id"] == "agent_t8"
    assert d["status"] == STATUS_RUNNING
