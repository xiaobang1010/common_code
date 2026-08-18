"""子代理任务管理工具 - GetSubagentOutput / StopSubagent。

主代理管理子代理任务的模型侧入口：查看输出（运行中为中间输出）、
单独停止（不影响父会话与其他任务）。与 REST 端点共用注册表。
"""

from __future__ import annotations

import logging

from pydantic import BaseModel

from tools.protocol import Tool, ToolResult, ToolUseContext, build_tool

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# GetSubagentOutput
# ---------------------------------------------------------------------------


class GetSubagentOutputInput(BaseModel):
    """GetSubagentOutput 输入。

    Attributes:
        agent_id: 子代理 ID
        max_chars: 返回的最大字符数（默认 30000）
    """

    agent_id: str
    max_chars: int = 30000


GET_SUBAGENT_OUTPUT_PROMPT = """\
获取子代理的输出。

使用说明：
- agent_id 是 Agent 工具返回的子代理标识
- 子代理运行中返回当前中间输出，已完成返回最终结果
- 结果过长时可用 max_chars 截取（默认保留开头 30000 字符）
"""


async def _get_output(inp: GetSubagentOutputInput, _context: ToolUseContext) -> ToolResult:
    """查看子代理输出：注册表最终文本优先，运行中取 transcript 中间输出。"""
    from tools.subagent.registry import get_subagent_registry
    from tools.subagent.transcript import get_agent_transcript

    task = get_subagent_registry().get(inp.agent_id)
    if task is None:
        return ToolResult(
            content=f"Subagent not found: {inp.agent_id}",
            is_error=True,
        )

    if task.final_text is not None:
        output = task.final_text
        source = "final"
    else:
        transcript = get_agent_transcript(inp.agent_id) or []
        output = ""
        for msg in reversed(transcript):
            if msg.get("role") == "assistant" and msg.get("content"):
                output = msg["content"]
                break
        source = "intermediate" if output else "empty"

    if not output:
        return ToolResult(
            content=f"Subagent {inp.agent_id} (status={task.status}) has no output yet.",
            metadata={"agent_id": inp.agent_id, "status": task.status},
        )

    max_chars = max(1, inp.max_chars)
    truncated = len(output) > max_chars
    body = output[:max_chars] + ("\n...(truncated)" if truncated else "")
    return ToolResult(
        content=(
            f"[Subagent {inp.agent_id} | status={task.status} | source={source}]\n{body}"
        ),
        metadata={
            "agent_id": inp.agent_id,
            "status": task.status,
            "source": source,
            "truncated": truncated,
        },
    )


def get_get_subagent_output_tool() -> Tool:
    """返回 GetSubagentOutput 工具实例。"""
    return build_tool(
        name="GetSubagentOutput",
        description="Get the output of a subagent (intermediate while running, final when completed)",
        input_schema=GetSubagentOutputInput,
        execute=_get_output,
        prompt=GET_SUBAGENT_OUTPUT_PROMPT,
        is_read_only=True,
    )


# ---------------------------------------------------------------------------
# StopSubagent
# ---------------------------------------------------------------------------


class StopSubagentInput(BaseModel):
    """StopSubagent 输入。

    Attributes:
        agent_id: 要停止的子代理 ID
    """

    agent_id: str


STOP_SUBAGENT_PROMPT = """\
停止单个子代理任务。

使用说明：
- agent_id 是 Agent 工具返回的子代理标识
- 仅停止该子代理，不影响当前会话与其他任务
- 子代理已结束时调用是无害的（幂等）
"""


async def _stop_subagent(inp: StopSubagentInput, _context: ToolUseContext) -> ToolResult:
    """停止单个子代理：后台取消任务句柄，前台置位 abort 事件。"""
    from tools.subagent.registry import (
        STATUS_STOPPED,
        TERMINAL_STATUSES,
        get_subagent_registry,
    )

    registry = get_subagent_registry()
    task = registry.get(inp.agent_id)
    if task is None:
        return ToolResult(
            content=f"Subagent not found: {inp.agent_id}",
            is_error=True,
        )

    if task.status in TERMINAL_STATUSES:
        return ToolResult(
            content=f"Subagent {inp.agent_id} already finished (status={task.status}).",
            metadata={"agent_id": inp.agent_id, "status": task.status},
        )

    # 后台任务：cancel asyncio 句柄（_run_background 记 stopped）
    if task.task is not None and not task.task.done():
        task.task.cancel()
        return ToolResult(
            content=f"Stop requested for background subagent {inp.agent_id}.",
            metadata={"agent_id": inp.agent_id, "status": "stopping"},
        )

    # 前台任务：置位 abort 事件（轮次边界优雅退出）
    if task.ctx is not None and task.ctx.abort_event is not None:
        task.ctx.abort_event.set()
        return ToolResult(
            content=f"Stop requested for subagent {inp.agent_id}.",
            metadata={"agent_id": inp.agent_id, "status": "stopping"},
        )

    registry.mark_status(inp.agent_id, STATUS_STOPPED, error="stopped by request")
    return ToolResult(
        content=f"Subagent {inp.agent_id} marked stopped.",
        metadata={"agent_id": inp.agent_id, "status": STATUS_STOPPED},
    )


def get_stop_subagent_tool() -> Tool:
    """返回 StopSubagent 工具实例。"""
    return build_tool(
        name="StopSubagent",
        description="Stop a running subagent by agent_id",
        input_schema=StopSubagentInput,
        execute=_stop_subagent,
        prompt=STOP_SUBAGENT_PROMPT,
        is_read_only=True,
    )
