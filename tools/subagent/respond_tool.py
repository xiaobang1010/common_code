"""RespondToCoordinator — 子代理对父会话的中途汇报工具。

星形拓扑：子代理不持有横向工具（SendMessage/任务管理工具不在其池内），
协调只能过父会话——父经 SendMessage 下行，子经本工具上行。
消息构造为 <subagent-message> 信封推入通知队列：父活跃时经既有轮次收尾
drain 注入父对话，父空闲时经唤起钩子自动起轮（与终态通知同一条管线）。
"""

from __future__ import annotations

import logging

from pydantic import BaseModel

from tools.protocol import Tool, ToolResult, ToolUseContext, build_tool

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 输入模型
# ---------------------------------------------------------------------------


class RespondToCoordinatorInput(BaseModel):
    """RespondToCoordinator 工具输入（唯一目标是父会话，无 to 字段）。

    Attributes:
        summary: 短摘要（3-10 词）
        message: 汇报正文
    """

    summary: str
    message: str


# ---------------------------------------------------------------------------
# 工具描述
# ---------------------------------------------------------------------------


RESPOND_TO_COORDINATOR_PROMPT = """\
Send a status message to the session that spawned you.

- Work continues after you send; this is a mid-run report channel, not your final answer.
- Your plain text output is not visible to the spawner — use this tool to communicate
  progress, findings worth surfacing early, or questions you are blocked on.
- Messages are delivered automatically; the spawner may reply to you via its own
  messaging tool, which will arrive in your conversation as a new message.
- There is no way to reach other agents directly: everything goes through the spawner.
"""


# ---------------------------------------------------------------------------
# execute
# ---------------------------------------------------------------------------


async def _execute(inp: RespondToCoordinatorInput, context: ToolUseContext) -> ToolResult:
    """向父会话通知队列推一条汇报信封；未绑定父会话时拒绝投递。"""
    from tools.subagent.notify import format_subagent_message, push_notification

    agent_id = getattr(context, "tool_use_id", "") or ""
    parent_session_id = getattr(context, "parent_session_id", "") or ""
    if not agent_id or not parent_session_id:
        return ToolResult(
            content="No coordinator session is bound to this agent; report unavailable.",
            is_error=True,
        )

    # agent_type 经注册表反查（上下文只携带定位信息），查不到时给通用标注
    agent_type = "subagent"
    try:
        from tools.subagent.registry import get_subagent_registry

        ctx = get_subagent_registry().get_ctx(agent_id)
        if ctx is not None:
            agent_type = ctx.agent_def.agent_type
    except Exception as e:  # noqa: BLE001 反查失败不影响投递
        logger.debug("汇报反查 agent_type 失败 (%s): %s", agent_id, e)

    push_notification(
        parent_session_id,
        format_subagent_message(agent_id, agent_type, inp.summary, inp.message),
    )
    return ToolResult(
        content=f"Report queued for the coordinator session ({agent_id}).",
        metadata={"agent_id": agent_id, "delivery": "queued"},
    )


# ---------------------------------------------------------------------------
# 工厂函数
# ---------------------------------------------------------------------------


def get_respond_to_coordinator_tool() -> Tool:
    """返回 RespondToCoordinator 工具实例。

    只读元数据必须为 True：声明 read-only 权限模式的代理会裁掉非只读工具，
    汇报通道不能被裁——裁掉它等于掐断该类代理唯一的上行链路。
    """
    return build_tool(
        name="RespondToCoordinator",
        description="Send a status message to the session that spawned you",
        input_schema=RespondToCoordinatorInput,
        execute=_execute,
        prompt=RESPOND_TO_COORDINATOR_PROMPT,
        is_read_only=True,
    )
