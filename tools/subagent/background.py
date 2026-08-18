"""后台子代理启动器 - 真后台运行。

asyncio 任务由注册表持有引用（防垃圾回收），统一异常处理，
完成/失败/停止均写入注册表，并向父会话通知队列投递完成通知。
"""

from __future__ import annotations

import asyncio
import logging
import time

from tools.protocol import Tool
from tools.subagent.context import SubagentContext
from tools.subagent.registry import (
    MODE_BACKGROUND,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_STOPPED,
    SubagentTask,
    get_subagent_registry,
)

logger = logging.getLogger(__name__)

# 后台结果截断阈值（与前台路径一致）
MAX_RESULT_SIZE_CHARS = 100_000


async def _consume_subagent(ctx: SubagentContext, tools: list[Tool], system_prompt: str) -> str:
    """消费 run_agent 消息流，返回最终 assistant 文本。"""
    from tools.subagent.runner import run_agent

    final_text = ""
    async for message in run_agent(ctx=ctx, tools=tools, system_prompt=system_prompt):
        if isinstance(message, dict) and message.get("role") == "assistant":
            content = message.get("content", "")
            if content:
                final_text = content
    return final_text


def _truncate_result(agent_id: str, final_text: str) -> tuple[str, str | None]:
    """超阈值截断并落盘，返回 (截断后文本, 落盘路径)。"""
    if len(final_text) <= MAX_RESULT_SIZE_CHARS:
        return final_text, None
    from tools.subagent.transcript import save_full_result
    result_path = save_full_result(agent_id, final_text)
    truncated = (
        final_text[:MAX_RESULT_SIZE_CHARS]
        + f"\n\n[Result truncated. Full output saved to: {result_path}]"
    )
    return truncated, result_path


def _format_notification(task: SubagentTask) -> dict:
    """构造投递给父会话的完成通知（最小 usage 字段）。"""
    duration_ms = int((task.updated_at - task.created_at) * 1000)
    usage = task.usage
    preview = (task.final_text or "")[:200]
    lines = [
        f"[后台子代理通知] {task.agent_id} (type={task.agent_type}) "
        f"状态: {task.status}",
        f"耗时: {duration_ms}ms, 工具调用: {usage.get('tool_uses', 0)} 次, "
        f"tokens: {usage.get('total_tokens', 0)}",
    ]
    if task.error:
        lines.append(f"原因: {task.error}")
    if preview:
        lines.append(f"结果预览: {preview}")
    lines.append(
        f"可用 GetSubagentOutput/SendMessage 按 agent_id={task.agent_id} 查看结果或续聊。"
    )
    return {"role": "user", "content": "\n".join(lines)}


def _notify_parent(task: SubagentTask) -> None:
    """向父会话通知队列投递完成通知（父会话活跃时由 loop drain 注入）。"""
    from tools.subagent import notify

    if task.parent_session_id:
        notify.push_notification(task.parent_session_id, _format_notification(task))


async def _run_background(task: SubagentTask, ctx: SubagentContext, tools: list[Tool], system_prompt: str) -> None:
    """后台任务体：运行子代理并统一处理终态（统一异常 handler）。"""
    registry = get_subagent_registry()
    # duration 由注册表时间戳推算，tokens/tool_uses 取 runner 汇总的 ctx.usage
    def _usage() -> dict[str, int]:
        usage = dict(ctx.usage)
        usage.setdefault("duration_ms", int((time.time() - task.created_at) * 1000))
        usage["duration_ms"] = max(
            usage.get("duration_ms", 0), int((time.time() - task.created_at) * 1000)
        )
        return usage

    try:
        final_text = await _consume_subagent(ctx, tools, system_prompt)
    except asyncio.CancelledError:
        # 单独 stop 或父环境取消 -> stopped
        registry.set_result(
            task.agent_id,
            status=STATUS_STOPPED,
            usage=_usage(),
            error="stopped by request",
        )
        _notify_parent(registry.get(task.agent_id))  # type: ignore[arg-type]
        raise
    except Exception as e:
        # 统一异常 handler：置 failed 并记录原因，异常不外泄炸掉事件循环
        logger.exception("后台子代理 %s 异常: %s", task.agent_id, e)
        registry.set_result(
            task.agent_id,
            status=STATUS_FAILED,
            error=str(e),
            usage=_usage(),
        )
        _notify_parent(registry.get(task.agent_id))  # type: ignore[arg-type]
        return

    final_text, output_file = _truncate_result(task.agent_id, final_text)
    registry.set_result(
        task.agent_id,
        status=STATUS_COMPLETED,
        final_text=final_text,
        output_file=output_file,
        usage=_usage(),
    )
    _notify_parent(registry.get(task.agent_id))  # type: ignore[arg-type]


def launch_background_subagent(
    ctx: SubagentContext,
    tools: list[Tool],
    system_prompt: str,
    *,
    description: str = "",
    parent_session_id: str | None = None,
) -> SubagentTask:
    """启动真后台子代理：注册、创建 asyncio 任务、注册表持有引用。

    Args:
        ctx: 子代理执行上下文（is_async=True，独立 abort 事件）
        tools: 过滤后的工具池
        system_prompt: 子代理系统提示词
        description: 任务简述
        parent_session_id: 父会话标识（通知投递与列表过滤用）

    Returns:
        SubagentTask 注册表任务记录
    """
    registry = get_subagent_registry()
    task = registry.register(
        ctx.agent_id,
        ctx,
        agent_type=ctx.agent_def.agent_type,
        description=description,
        mode=MODE_BACKGROUND,
        parent_session_id=parent_session_id,
    )
    async_task = asyncio.create_task(_run_background(task, ctx, tools, system_prompt))
    registry.attach_task(ctx.agent_id, async_task)
    logger.info(
        "后台子代理已启动 %s (parent_session=%s)", ctx.agent_id, parent_session_id
    )
    return task
