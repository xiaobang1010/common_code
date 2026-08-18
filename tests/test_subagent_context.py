"""子代理上下文语义测试 - is_subagent_context 判定与执行器防递归兜底。"""

from __future__ import annotations

import asyncio

from pydantic import BaseModel

from tools.executor import execute_tool_call
from tools.protocol import ToolResult, ToolUseContext, build_tool
from tools.subagent.tools import is_subagent_context


class _DummyInput(BaseModel):
    """空输入模型。"""

    prompt: str = ""


def test_is_subagent_context_by_prefix():
    """agent_ 前缀的 tool_use_id 判定为子代理上下文，空/其他前缀不是。"""
    assert is_subagent_context(ToolUseContext(tool_use_id="agent_ab12")) is True
    assert is_subagent_context(ToolUseContext(tool_use_id="")) is False
    assert is_subagent_context(ToolUseContext(tool_use_id="call_9")) is False
    assert is_subagent_context(None) is False


def test_subagent_loop_context_carries_agent_id():
    """runner 传入的上下文在 query_loop 每轮执行器构建时继承 tool_use_id。

    直接验证 query_loop 的参数透传逻辑：构建一轮最小引擎跑通循环成本高，
    这里断言 SubagentContext.tool_use_context 的 tool_use_id 等于 agent_id
    （该值经 query_loop(tool_use_context=...) 继承到每轮执行器）。
    """
    from tools.subagent.context import AgentDefinition, create_subagent_context

    agent_def = AgentDefinition(agent_type="general-purpose", when_to_use="测试")
    ctx = create_subagent_context(
        parent_context=None,
        agent_def=agent_def,
        main_loop_model="test-model",
        agent_id="agent_loop01",
        prompt="任务",
    )
    assert ctx.tool_use_context.tool_use_id == "agent_loop01"
    assert is_subagent_context(ctx.tool_use_context) is True


def _make_agent_tool() -> object:
    """构造一个最小 Agent 工具用于防递归测试。"""

    async def _execute(_inp, _context):
        return ToolResult(content="should not reach here")

    return build_tool(
        name="Agent",
        description="派生子代理",
        input_schema=_DummyInput,
        execute=_execute,
        prompt="",
        aliases=["Task"],
    )


def test_executor_blocks_agent_dispatch_in_subagent_context():
    """子代理上下文内调用 Agent/Task 被执行器兜底拒绝。"""
    tool = _make_agent_tool()

    async def main():
        # 子代理上下文（tool_use_id 带 agent_ 前缀）
        sub_ctx = ToolUseContext(tool_use_id="agent_inner1")
        result = await execute_tool_call(
            tool_call={
                "id": "call_1",
                "function": {"name": "Agent", "arguments": "{}"},
            },
            tools=[tool],
            context=sub_ctx,
        )
        assert result.is_error is True
        assert "Nested subagent dispatch" in result.content

        # 别名 Task 同样被拦截
        result_alias = await execute_tool_call(
            tool_call={
                "id": "call_2",
                "function": {"name": "Task", "arguments": "{}"},
            },
            tools=[tool],
            context=sub_ctx,
        )
        assert result_alias.is_error is True

    asyncio.run(main())


def test_executor_allows_agent_dispatch_in_main_context():
    """主循环上下文（tool_use_id 为空）调用 Agent 不受兜底影响。"""
    tool = _make_agent_tool()

    async def main():
        main_ctx = ToolUseContext(tool_use_id="")
        result = await execute_tool_call(
            tool_call={
                "id": "call_3",
                "function": {"name": "Agent", "arguments": "{}"},
            },
            tools=[tool],
            context=main_ctx,
        )
        assert result.is_error is False
        assert result.content == "should not reach here"

    asyncio.run(main())
