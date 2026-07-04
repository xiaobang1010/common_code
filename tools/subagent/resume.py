"""子代理 resume — 从 transcript 恢复上下文继续运行。

当 SendMessage 到已停止的子代理时，从磁盘 transcript 加载历史消息，
追加新 prompt，重新运行子代理。

参考 Claude Code 的 resumeAgentBackground 流程。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from tools.protocol import ToolUseContext
from tools.subagent.context import SubagentContext, create_subagent_context
from tools.subagent.transcript import get_agent_transcript, read_agent_metadata

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 活跃子代理注册表（进程内）
# ---------------------------------------------------------------------------

# agent_id → {"ctx": SubagentContext, "status": "running"/"stopped"}
_active_subagents: dict[str, dict[str, Any]] = {}


def register_subagent(agent_id: str, ctx: SubagentContext) -> None:
    """注册活跃子代理。"""
    _active_subagents[agent_id] = {"ctx": ctx, "status": "running"}


def mark_subagent_stopped(agent_id: str) -> None:
    """标记子代理为已停止。"""
    if agent_id in _active_subagents:
        _active_subagents[agent_id]["status"] = "stopped"


def unregister_subagent(agent_id: str) -> None:
    """注销子代理。"""
    _active_subagents.pop(agent_id, None)


def get_subagent_status(agent_id: str) -> str | None:
    """获取子代理状态。None 表示不在注册表中。"""
    entry = _active_subagents.get(agent_id)
    return entry["status"] if entry else None


def get_subagent_ctx(agent_id: str) -> SubagentContext | None:
    """获取子代理上下文。"""
    entry = _active_subagents.get(agent_id)
    return entry["ctx"] if entry else None


def queue_pending_message(agent_id: str, message: str) -> bool:
    """向正在运行的子代理的消息队列追加消息。

    Returns:
        True 成功入队，False 子代理不在运行中
    """
    entry = _active_subagents.get(agent_id)
    if entry is None or entry["status"] != "running":
        return False
    entry["ctx"].pending_messages.append(message)
    return True


# ---------------------------------------------------------------------------
# resume_agent_background — 从 transcript 恢复
# ---------------------------------------------------------------------------


async def resume_agent_background(
    agent_id: str,
    prompt: str,
    parent_context: ToolUseContext | None = None,
) -> str:
    """从 transcript 恢复子代理，追加新 prompt 继续运行。

    流程：
    1. 调 get_agent_transcript 加载历史消息
    2. 过滤未完成的 tool_use
    3. 读取 meta.json 获取 agent_type
    4. 查找 AgentDefinition
    5. 创建新的 SubagentContext（传入历史消息 + 新 prompt）
    6. 调 run_agent 继续执行

    Args:
        agent_id: 子代理 ID
        prompt: 新的任务指令
        parent_context: 父代理上下文（用于模型解析等）

    Returns:
        子代理的最终 assistant 文本
    """
    # 1. 加载历史消息
    resumed_messages = get_agent_transcript(agent_id)
    if resumed_messages is None:
        raise RuntimeError(f"No transcript found for agent: {agent_id}")

    # 2. 读取元数据
    meta = read_agent_metadata(agent_id)
    agent_type = meta.get("agent_type", "general-purpose") if meta else "general-purpose"

    # 3. 查找代理定义
    from tools.subagent.built_in_agents import find_agent_by_type
    agent_def = find_agent_by_type(agent_type)
    if agent_def is None:
        # 找不到原始类型，降级为 general-purpose
        agent_def = find_agent_by_type("general-purpose")

    # 4. 获取主循环模型
    from query.services.api.client import get_default_model
    main_loop_model = get_default_model()

    # 5. 创建上下文（传入历史消息 + 新 prompt）
    ctx = create_subagent_context(
        parent_context=parent_context,
        agent_def=agent_def,
        main_loop_model=main_loop_model,
        agent_id=agent_id,
        is_async=True,  # resume 的是异步的
        prompt="",  # prompt 单独追加
    )

    # 追加历史消息 + 新 prompt
    ctx.initial_messages = resumed_messages + [{"role": "user", "content": prompt}]

    # 注册为活跃
    register_subagent(agent_id, ctx)

    # 6. 运行子代理
    from tools.subagent.runner import run_agent
    from tools import get_tools
    from tools.subagent.tools import resolve_agent_tools

    all_tools = get_tools()
    worker_tools = resolve_agent_tools(agent_def, all_tools)
    system_prompt = agent_def.resolve_system_prompt()

    final_text = ""
    try:
        async for message in run_agent(ctx, worker_tools, system_prompt):
            if isinstance(message, dict) and message.get("role") == "assistant":
                content = message.get("content", "")
                if content:
                    final_text = content
    finally:
        mark_subagent_stopped(agent_id)

    return final_text
