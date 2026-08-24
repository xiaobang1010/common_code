"""SendMessage 工具 — 向子代理发消息续接。

三路径分发：
- running：消息入队 pending_messages，下一轮 tool 边界投递
- stopped：从 transcript resume，追加 prompt 继续运行
- evicted：从磁盘 transcript 恢复，重新运行
"""

from __future__ import annotations

import asyncio
import logging

from pydantic import BaseModel

from tools.protocol import Tool, ToolResult, ToolUseContext, build_tool

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 输入模型
# ---------------------------------------------------------------------------


class SendMessageInput(BaseModel):
    """SendMessage 工具输入。

    Attributes:
        to: 目标子代理的 agent_id 或名字
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
向子代理发送消息。

使用说明：
- to 是子代理的 agent_id
- summary 是 3-5 词的简短摘要
- message 是完整消息内容
- 如果子代理正在运行，消息会入队并在下一轮 tool 边界投递
- 如果子代理已停止，会从 transcript 恢复上下文继续运行
"""


# ---------------------------------------------------------------------------
# execute
# ---------------------------------------------------------------------------


async def _execute(inp: SendMessageInput, context: ToolUseContext) -> ToolResult:
    """执行消息发送，按三路径分发。"""
    from tools.subagent.registry import get_subagent_registry
    from tools.subagent.resume import resume_agent_background

    agent_id = inp.to
    registry = get_subagent_registry()

    # 路径 1：子代理正在运行 -> 入队
    if registry.get_status(agent_id) == "running":
        ok = registry.queue_pending_message(agent_id, inp.message)
        if ok:
            return ToolResult(
                content=f"Message queued for delivery to {agent_id} at its next tool round.",
            )
        # 入队失败，降级到 resume

    # 路径 2 & 3：已停止或不在内存 → 从 transcript resume
    try:
        result = await resume_agent_background(
            agent_id=agent_id,
            prompt=inp.message,
            parent_context=context,
        )
        return ToolResult(
            content=result or f"Agent {agent_id} resumed with no output.",
            metadata={"agent_id": agent_id, "status": "resumed"},
        )
    except RuntimeError as e:
        # transcript 不存在
        return ToolResult(
            content=f"Agent not found: {agent_id}. {e}",
            is_error=True,
        )
    except Exception as e:
        return ToolResult(
            content=f"Failed to resume agent {agent_id}: {e}",
            is_error=True,
        )


# ---------------------------------------------------------------------------
# 工厂函数
# ---------------------------------------------------------------------------


def get_send_message_tool() -> Tool:
    """返回 SendMessage 工具实例。"""
    return build_tool(
        name="SendMessage",
        description="Send a message to a subagent (resume if stopped)",
        input_schema=SendMessageInput,
        execute=_execute,
        prompt=SEND_MESSAGE_PROMPT,
        is_read_only=True,
    )
