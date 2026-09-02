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
# 并发限制
# ---------------------------------------------------------------------------

# 每会话与全局运行中子代理数上限，超限明确报错（不静默排队、不假启动）
MAX_SUBAGENTS_PER_SESSION = 4
MAX_SUBAGENTS_GLOBAL = 16


def _check_concurrency(context: ToolUseContext) -> str | None:
    """检查并发上限，超限返回错误信息，未超限返回 None。"""
    from tools.subagent.registry import get_subagent_registry

    registry = get_subagent_registry()
    global_running = registry.running_count()
    if global_running >= MAX_SUBAGENTS_GLOBAL:
        return (
            f"Too many concurrent subagents (global limit: {MAX_SUBAGENTS_GLOBAL}). "
            "Wait for running subagents to finish or stop them first."
        )
    if context.session_id:
        session_running = registry.running_count(session_id=context.session_id)
        if session_running >= MAX_SUBAGENTS_PER_SESSION:
            return (
                f"Too many concurrent subagents for this session "
                f"(limit: {MAX_SUBAGENTS_PER_SESSION}). "
                "Wait for running subagents to finish or stop them first."
            )
    return None


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
    # 解析代理类型：精确/唯一模糊命中才继续，歧义与未命中把候选回给模型自我纠正
    from tools.subagent.resolver import resolve_agent_type

    resolved = resolve_agent_type(inp.subagent_type)
    if resolved.kind != "matched" or resolved.agent is None:
        return ToolResult(
            content=resolved.error_text(inp.subagent_type),
            is_error=True,
        )
    agent_def = resolved.agent

    # 并发限制：超限明确报错，不静默排队、不假启动
    concurrency_error = _check_concurrency(context)
    if concurrency_error is not None:
        return ToolResult(content=concurrency_error, is_error=True)

    # profile 声明 background:true 时自动走后台（输入未显式指定时）
    if agent_def.background and not inp.run_in_background:
        inp = inp.model_copy(update={"run_in_background": True})

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

    # ---- 普通 subagent 派生路径（生命周期引擎统一入口）----
    from tools.subagent.lifecycle import SpawnRequest, spawn_subagent

    spawn_result = await spawn_subagent(
        SpawnRequest(
            agent_def=agent_def,
            prompt=inp.prompt,
            description=inp.description,
            parent_context=context,
            run_in_background=inp.run_in_background,
        )
    )

    # 后台/被提升：立即返回 agent_id，完成后自动通知
    if spawn_result.kind == "async_launched":
        return ToolResult(
            content=(
                f"Subagent launched in background "
                f"(agent_id: {spawn_result.agent_id}). "
                f"结果将在完成后自动通知；也可用 GetSubagentOutput 查看中间输出，"
                f"StopSubagent 停止，SendMessage 续聊。"
            ),
            is_error=False,
            metadata={
                "agent_id": spawn_result.agent_id,
                "status": "async_launched",
            },
        )

    # 前台完成：生命周期引擎已处理终态（注册表/子会话/通知/截断），
    # 这里只负责给模型的返回格式
    outcome = spawn_result.outcome
    assert outcome is not None
    agent_id = spawn_result.agent_id
    final_status = outcome.status
    result_content = outcome.final_text or "Subagent completed with no output"

    # 结果开头放 agent_id 行：任何头截断（前端 500 字符展示、executor 预算）都切不到它，
    # 前端状态卡片据此解析任务
    result_content = (
        f"[subagent_id: {agent_id} | status: {final_status}]\n\n" + result_content
    )
    # 尾部附 usage 统计与续聊指引（引导用 SendMessage 续聊而非重开）
    usage = (spawn_result.task.usage if spawn_result.task is not None else {}) or {}
    result_content += (
        f"\n\n--- \n"
        f"subagent_id: {agent_id} | status: {final_status} | "
        f"tokens: {usage.get('total_tokens', 0)} | "
        f"tool_uses: {usage.get('tool_uses', 0)} | "
        f"duration_ms: {usage.get('duration_ms', 0)}\n"
        f"如需继续该子代理的上下文，用 SendMessage 发送 agent_id={agent_id} 续聊。"
    )

    return ToolResult(
        content=result_content,
        is_error=outcome.status not in ("completed",),
        metadata={
            "agent_id": agent_id,
            "agent_type": agent_def.agent_type,
            "status": final_status,
            "usage": dict(usage),
        },
    )


# ---------------------------------------------------------------------------
# 工厂函数
# ---------------------------------------------------------------------------


def get_agent_tool() -> Tool:
    """返回 AgentTool 实例。"""
    from tools.protocol import TimeoutPolicy

    return build_tool(
        name="Agent",
        description="Launch a subagent to handle a task",
        input_schema=AgentInput,
        execute=_execute,
        prompt=AGENT_TOOL_PROMPT,
        validate_input=_validate_input,
        is_read_only=True,  # 权限委托给底层工具
        aliases=["Task"],
        # 子代理是多轮长任务（搜索/阅读/汇总），豁免通用工具 125s 安全网，
        # 改用 30 分钟大阈值；终止依赖 abort_event 优雅退出（见 1.4 接线）
        timeout_policy=TimeoutPolicy(default_ms=1_800_000, max_ms=1_800_000),
    )
