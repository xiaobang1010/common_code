"""SendMessage 工具 — 向子代理发消息续接。

三路径分发：
- running：消息入队，短窗口内等注入确认——确认返回 delivered，
  未确认返回 queued（下个安全点仍会注入）
- stopped：从 transcript 后台 resume，返回 resumed_background
- evicted：从磁盘 transcript 恢复，同 resume 路径
"""

from __future__ import annotations

import asyncio
import logging

from pydantic import BaseModel

from tools.protocol import Tool, ToolResult, ToolUseContext, build_tool

logger = logging.getLogger(__name__)

# delivered 判定的短窗口：入队后等待 runner 注入确认的最长时间
DELIVERY_ACK_WINDOW_S = 2.0


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
- 如果子代理正在运行，消息会入队并在下个轮次边界注入（返回 delivered/queued）
- 如果子代理已停止，会在后台从 transcript 恢复上下文继续运行（resumed_background）
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

    # 路径 1：子代理正在运行 → 入队 + 短窗口等注入确认
    if registry.get_status(agent_id) == "running":
        ok = registry.queue_pending_message(agent_id, inp.message)
        if ok:
            delivery = await _await_delivery_ack(registry, agent_id)
            return ToolResult(
                content=(
                    f"Message {'delivered to' if delivery == 'delivered' else 'queued for'} "
                    f"{agent_id}"
                    + (
                        "."
                        if delivery == "delivered"
                        else " — will be injected at its next turn boundary."
                    )
                ),
                metadata={"agent_id": agent_id, "delivery": delivery},
            )
        # 入队失败（状态竞态），降级到 resume 路径

    # 路径 2 & 3：已停止或不在内存 → 从 transcript 后台 resume
    try:
        result = await resume_agent_background(
            agent_id=agent_id,
            prompt=inp.message,
            parent_context=context,
        )
        return ToolResult(
            content=result or f"Agent {agent_id} resumed with no output.",
            metadata={"agent_id": agent_id, "delivery": "resumed_background"},
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


async def _await_delivery_ack(registry, agent_id: str) -> str:
    """短窗口等待注入确认：队列被 runner 清空即 delivered，超时返回 queued。

    注入由 runner 在轮次边界完成；无论返回哪个状态消息最终都会被注入，
    两态只是把「是否已进对话」的确定性告知调用方。
    """
    loop = asyncio.get_event_loop()
    deadline = loop.time() + DELIVERY_ACK_WINDOW_S
    while loop.time() < deadline:
        ctx = registry.get_ctx(agent_id)
        if ctx is None or not ctx.pending_messages:
            return "delivered"
        await asyncio.sleep(0.05)
    return "queued"


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
