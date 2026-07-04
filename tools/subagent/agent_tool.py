"""AgentTool — 子代理派生工具入口。

主 LLM 通过此工具派生子代理执行隔离上下文的子任务。
当 team_name + name 同时存在时走 teammate 派生路径（Multi-Agent），
否则走普通 subagent 路径。

同步路径：调 run_agent 收集最终 assistant 文本作为 tool_result。
异步路径：立即返回 agent_id，后台执行。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from tools.protocol import Tool, ToolResult, ToolUseContext, build_tool
from tools.subagent.built_in_agents import find_agent_by_type
from tools.subagent.context import create_subagent_context
from tools.subagent.types import AgentDefinition


# ---------------------------------------------------------------------------
# 输入模型
# ---------------------------------------------------------------------------


class AgentInput(BaseModel):
    """Agent 工具输入。

    Attributes:
        description: 3-5 词任务描述
        prompt: 给子代理的任务指令
        subagent_type: 代理类型（如 "general-purpose"、"Explore"），默认 general-purpose
        run_in_background: 是否后台运行（异步），默认 False
        team_name: 团队名（teammate 派生时必填）
        name: teammate 名字（teammate 派生时必填）
    """

    description: str
    prompt: str
    subagent_type: str = "general-purpose"
    run_in_background: bool = False
    team_name: str | None = None
    name: str | None = None


# ---------------------------------------------------------------------------
# 工具描述
# ---------------------------------------------------------------------------


AGENT_TOOL_PROMPT = """\
派生子代理执行隔离上下文的子任务。

使用说明：
- description 是 3-5 词的简短任务描述
- prompt 是给子代理的完整任务指令
- subagent_type 选择代理类型：
  - general-purpose：全工具，通用研究/多步骤任务
  - Explore：只读搜索，快速定位代码库信息
- 需要并行执行多个独立子任务时，在一条消息中发起多个 Agent 工具调用
- 子代理结果对用户不可见，需要你转述关键发现
- run_in_background=true 时子代理后台运行，立即返回 agent_id
"""


# ---------------------------------------------------------------------------
# validate_input
# ---------------------------------------------------------------------------


def _validate_input(inp: AgentInput, _context: ToolUseContext) -> dict[str, Any]:
    """校验输入。"""
    if not inp.prompt.strip():
        return {"result": False, "message": "prompt 不能为空"}
    if not inp.description.strip():
        return {"result": False, "message": "description 不能为空"}
    # teammate 派生需要 team_name 和 name 同时存在
    if (inp.team_name is None) != (inp.name is None):
        return {
            "result": False,
            "message": "team_name 和 name 必须同时指定或同时省略",
        }
    return {"result": True}


# ---------------------------------------------------------------------------
# execute
# ---------------------------------------------------------------------------


async def _execute(inp: AgentInput, context: ToolUseContext) -> ToolResult:
    """执行子代理派生。

    路由逻辑：
    1. team_name + name 同时存在 → teammate 派生（阶段四实现）
    2. 否则 → 普通 subagent 派生（阶段三实现 run_agent）

    同步路径收集最终 assistant 文本作为 tool_result。
    异步路径立即返回 agent_id。
    """
    # 解析代理类型
    agent_def = find_agent_by_type(inp.subagent_type)
    if agent_def is None:
        return ToolResult(
            content=f"Agent type not found: {inp.subagent_type}",
            is_error=True,
        )

    # ---- teammate 派生路径（阶段四实现）----
    if inp.team_name and inp.name:
        try:
            from tools.team.lifecycle import spawn_teammate
            result = await spawn_teammate(
                team_name=inp.team_name,
                agent_name=inp.name,
                prompt=inp.prompt,
                agent_def=agent_def,
                parent_context=context,
            )
            return ToolResult(
                content=result,
                is_error=False,
            )
        except ImportError:
            return ToolResult(
                content="Teammate spawn not yet implemented (requires team module)",
                is_error=True,
            )

    # ---- 普通 subagent 派生路径 ----
    # 获取主循环模型名
    from query.services.api.client import get_default_model
    main_loop_model = get_default_model()

    # 创建隔离上下文
    subagent_ctx = create_subagent_context(
        parent_context=context,
        agent_def=agent_def,
        main_loop_model=main_loop_model,
        is_async=inp.run_in_background,
        prompt=inp.prompt,
    )

    # 异步路径：立即返回 agent_id
    if inp.run_in_background:
        # 阶段三实现后台运行逻辑，先返回 agent_id
        return ToolResult(
            content=f"Subagent launched in background (agent_id: {subagent_ctx.agent_id})",
            is_error=False,
            metadata={"agent_id": subagent_ctx.agent_id, "status": "async_launched"},
        )

    # 同步路径：调 run_agent 收集最终结果
    try:
        from tools.subagent.runner import run_agent
    except ImportError:
        return ToolResult(
            content="Subagent runner not yet implemented (requires runner module)",
            is_error=True,
        )

    # 解析系统提示词
    system_prompt = agent_def.resolve_system_prompt()

    # 获取工具池（阶段三实现工具过滤）
    from tools import get_tools
    all_tools = get_tools()

    from tools.subagent.tools import resolve_agent_tools
    worker_tools = resolve_agent_tools(agent_def, all_tools)

    # 运行子代理，收集最终 assistant 文本
    final_text = ""
    async for message in run_agent(
        ctx=subagent_ctx,
        tools=worker_tools,
        system_prompt=system_prompt,
    ):
        if isinstance(message, dict) and message.get("role") == "assistant":
            content = message.get("content", "")
            if content:
                final_text = content  # 保留最后一条 assistant 消息

    # 结果截断保护：超过阈值时截断并落盘
    MAX_RESULT_SIZE_CHARS = 100_000
    if len(final_text) > MAX_RESULT_SIZE_CHARS:
        from tools.subagent.transcript import save_full_result
        result_path = save_full_result(subagent_ctx.agent_id, final_text)
        final_text = (
            final_text[:MAX_RESULT_SIZE_CHARS]
            + f"\n\n[Result truncated. Full output saved to: {result_path}]"
        )

    return ToolResult(
        content=final_text or "Subagent completed with no output",
        is_error=False,
        metadata={
            "agent_id": subagent_ctx.agent_id,
            "agent_type": agent_def.agent_type,
            "status": "completed",
        },
    )


# ---------------------------------------------------------------------------
# 工厂函数
# ---------------------------------------------------------------------------


def get_agent_tool() -> Tool:
    """返回 AgentTool 实例。"""
    return build_tool(
        name="Agent",
        description="Launch a subagent to handle a task",
        input_schema=AgentInput,
        execute=_execute,
        prompt=AGENT_TOOL_PROMPT,
        validate_input=_validate_input,
        is_read_only=True,  # 权限委托给底层工具
        aliases=["Task"],
    )
