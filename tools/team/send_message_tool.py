"""SendMessage 工具 — 代理间双向通信。

支持 teammate 名字寻址（单发）和广播（to="*"）。
结构化协议消息（shutdown_request）走专门路径。
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel

from tools.protocol import Tool, ToolResult, ToolUseContext, build_tool

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 输入模型
# ---------------------------------------------------------------------------


class SendMessageInput(BaseModel):
    """SendMessage 工具输入。

    Attributes:
        to: 接收者名字，或 "*" 广播
        summary: 消息摘要（3-5 词）
        message: 消息正文
    """

    to: str
    summary: str
    message: str


# ---------------------------------------------------------------------------
# 工具描述
# ---------------------------------------------------------------------------


SEND_MESSAGE_PROMPT = """\
向团队成员发送消息。

使用说明：
- to 是 teammate 名字，或 "*" 广播到所有成员
- summary 是 3-5 词的简短摘要
- message 是完整消息内容
- 消息会投递到 teammate 的邮箱，teammate 收到后作为新对话轮次处理
- 可以发送结构化消息：在 message 中包含 "[shutdown_request]" 来请求 teammate 退出
"""


# ---------------------------------------------------------------------------
# execute
# ---------------------------------------------------------------------------


async def _execute(inp: SendMessageInput, context: ToolUseContext) -> ToolResult:
    """执行消息发送。

    路由：
    - to="*" → 广播
    - to=具体名字 → 单发
    - message 含 "[shutdown_request]" → 结构化协议消息
    """
    # 从上下文获取当前团队和发送者
    team_name = _get_current_team_from_context(context)
    if team_name is None:
        return ToolResult(
            content="No active team. Create a team first with TeamCreate.",
            is_error=True,
        )

    sender = _get_sender_name_from_context(context)

    # 检测结构化协议消息
    is_shutdown = "[shutdown_request]" in inp.message

    # 广播
    if inp.to == "*":
        from tools.team.mailbox import broadcast

        sent_to = broadcast(
            team_name, inp.message,
            sender=sender, summary=inp.summary,
            exclude=sender,
        )
        return ToolResult(
            content=f"Message broadcast to {len(sent_to)} members: {', '.join(sent_to)}",
            metadata={"broadcast": True, "sent_to": sent_to},
        )

    # 单发
    from tools.team.mailbox import write_to_mailbox
    from tools.team.manager import get_member_names

    members = get_member_names(team_name)
    if inp.to not in members:
        return ToolResult(
            content=f"Recipient '{inp.to}' not found in team. Members: {', '.join(members)}",
            is_error=True,
        )

    msg_type = "shutdown_request" if is_shutdown else "normal"
    write_to_mailbox(
        team_name, inp.to, inp.message,
        sender=sender, summary=inp.summary,
        msg_type=msg_type,
    )

    return ToolResult(
        content=f"Message sent to {inp.to}",
        metadata={"to": inp.to, "msg_type": msg_type},
    )


# ---------------------------------------------------------------------------
# 上下文辅助
# ---------------------------------------------------------------------------


def _get_current_team_from_context(context: ToolUseContext) -> str | None:
    """从上下文获取当前团队名。"""
    from tools.team.manager import get_current_team
    return get_current_team()


def _get_sender_name_from_context(context: ToolUseContext) -> str:
    """从上下文获取发送者名字。"""
    # 主循环的发送者是 "leader"
    # teammate 的发送者通过 tool_use_id 判断
    tool_use_id = getattr(context, "tool_use_id", "")
    if tool_use_id and tool_use_id.startswith("agent_"):
        # 子代理上下文，用 agent_id 作为名字
        return tool_use_id
    return "leader"


# ---------------------------------------------------------------------------
# 工厂函数
# ---------------------------------------------------------------------------


def get_send_message_tool() -> Tool:
    """返回 SendMessageTool 实例。"""
    return build_tool(
        name="SendMessage",
        description="Send a message to a teammate or broadcast",
        input_schema=SendMessageInput,
        execute=_execute,
        prompt=SEND_MESSAGE_PROMPT,
        is_read_only=True,
    )
