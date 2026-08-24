"""Teammate 生命周期管理 — 派生、空闲等待、唤醒、shutdown。

teammate 派生走 spawn_teammate（区别于普通 run_agent），
注册团队成员、启动 inbox 轮询、扁平结构约束。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from tools.protocol import ToolUseContext
from tools.subagent.built_in_agents import find_agent_by_type
from tools.subagent.context import create_subagent_context
from tools.subagent.types import AgentDefinition

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 活跃 teammate 注册表（进程内）
# ---------------------------------------------------------------------------

# agent_name → {"ctx": SubagentContext, "poller": InboxPoller, "status": "running"/"idle"/"stopped"}
_active_teammates: dict[str, dict[str, Any]] = {}


# ---------------------------------------------------------------------------
# spawn_teammate — 派生 teammate
# ---------------------------------------------------------------------------


async def spawn_teammate(
    team_name: str,
    agent_name: str,
    prompt: str,
    agent_def: AgentDefinition,
    parent_context: ToolUseContext,
) -> str:
    """派生 teammate（区别于普通 subagent）。

    流程：
    1. 校验团队存在
    2. 校验扁平结构（teammate 不能派生 teammate）
    3. 注册团队成员
    4. 创建隔离上下文
    5. 启动 inbox 轮询
    6. 运行子代理循环（首轮用 prompt）
    7. 完成后进入空闲等待

    Args:
        team_name: 团队名
        agent_name: teammate 名字
        prompt: 首轮任务指令
        agent_def: 代理类型定义
        parent_context: 父代理上下文

    Returns:
        teammate 的状态消息
    """
    from tools.team.manager import team_exists, add_member

    # 1. 校验团队存在
    if not team_exists(team_name):
        return f"Team not found: {team_name}"

    # 2. 校验扁平结构（teammate 不能派生 teammate）
    parent_id = getattr(parent_context, "tool_use_id", "")
    if parent_id and parent_id.startswith("agent_"):
        return "Teammate cannot spawn teammate (team roster must be flat)"

    # 3. 获取主循环模型
    from query.services.api.client import get_default_model
    main_loop_model = get_default_model()

    # 4. 创建隔离上下文（共享父会话中断事件，/api/abort 时优雅退出）
    parent_abort_event = (
        parent_context.abort_controller
        if parent_context is not None
        and isinstance(parent_context.abort_controller, asyncio.Event)
        else None
    )
    ctx = create_subagent_context(
        parent_context=parent_context,
        agent_def=agent_def,
        main_loop_model=main_loop_model,
        agent_id=f"{agent_name}@{team_name}",
        depth=1,
        prompt=prompt,
        parent_abort_event=parent_abort_event,
    )

    # 5. 注册团队成员
    add_member(team_name, agent_name, ctx.agent_id)

    # 6. 启动 inbox 轮询
    from tools.team.inbox_poller import InboxPoller

    poller = InboxPoller(
        team_name=team_name,
        agent_name=agent_name,
        on_message=lambda msg: _handle_inbox_message(team_name, agent_name, msg),
        on_protocol=lambda msg: _handle_protocol_message(team_name, agent_name, msg),
    )
    poller.start()

    # 7. 注册到活跃列表
    _active_teammates[agent_name] = {
        "ctx": ctx,
        "poller": poller,
        "status": "running",
        "team_name": team_name,
    }

    logger.info("Teammate 派生: %s (team=%s, type=%s)",
                agent_name, team_name, agent_def.agent_type)

    # 检查是否已有 transcript（resume 场景）
    from tools.subagent.transcript import get_agent_transcript
    existing_transcript = get_agent_transcript(ctx.agent_id)
    if existing_transcript is not None:
        logger.info("Teammate %s 从 transcript resume", agent_name)
        ctx.initial_messages = existing_transcript + ctx.initial_messages

    # 8. 运行首轮子代理循环
    try:
        from tools.subagent.runner import run_agent
        from tools import get_tools, ToolContextFilter
        from tools.subagent.tools import resolve_agent_tools

        # teammate 工具池：移除 TeamCreate（不能建子团队），标记为 teammate 上下文
        all_tools = get_tools(ToolContextFilter(is_subagent=True, is_teammate=True, agent_type=agent_def.agent_type))
        worker_tools = resolve_agent_tools(agent_def, all_tools)
        system_prompt = agent_def.resolve_system_prompt()

        # teammate 标记忙碌
        poller.set_busy(True)

        async for _message in run_agent(ctx, worker_tools, system_prompt):
            pass  # 消息在子代理内部处理

        # 首轮完成，进入空闲
        _active_teammates[agent_name]["status"] = "idle"
        poller.set_busy(False)

        # 发送 idle notification 给 leader
        from tools.team.mailbox import write_to_mailbox
        from tools.team.manager import get_current_team
        team = get_current_team()
        if team:
            write_to_mailbox(
                team, "leader",
                f"teammate {agent_name} 完成了一轮工作，进入空闲状态",
                sender=agent_name,
                summary=f"{agent_name} idle",
                msg_type="idle_notification",
            )

    except Exception as e:
        logger.exception("Teammate %s 执行异常: %s", agent_name, e)
        _active_teammates[agent_name]["status"] = "stopped"
        poller.set_busy(False)

    return f"Teammate '{agent_name}' spawned and completed initial task (now idle)"


# ---------------------------------------------------------------------------
# _handle_inbox_message — 处理 inbox 消息
# ---------------------------------------------------------------------------


def _handle_inbox_message(team_name: str, agent_name: str, msg: dict) -> None:
    """处理收到的普通消息——唤醒 teammate 继续对话。"""
    teammate = _active_teammates.get(agent_name)
    if teammate is None:
        logger.warning("收到消息但 teammate %s 不存在", agent_name)
        return

    if teammate["status"] == "stopped":
        # 自动唤醒
        logger.info("唤醒已停止的 teammate: %s", agent_name)
        teammate["status"] = "idle"

    if teammate["status"] != "idle":
        logger.debug("Teammate %s 忙碌，消息排队", agent_name)
        return

    # 在后台运行新一轮
    asyncio.create_task(_run_teammate_round(team_name, agent_name, msg))


# ---------------------------------------------------------------------------
# _handle_protocol_message — 处理协议消息
# ---------------------------------------------------------------------------


def _handle_protocol_message(team_name: str, agent_name: str, msg: dict) -> None:
    """处理结构化协议消息（如 shutdown_request）。"""
    msg_type = msg.get("msg_type")

    if msg_type == "shutdown_request":
        logger.info("Teammate %s 收到 shutdown 请求", agent_name)
        _shutdown_teammate(agent_name)


# ---------------------------------------------------------------------------
# _run_teammate_round — 运行 teammate 一轮对话
# ---------------------------------------------------------------------------


async def _run_teammate_round(
    team_name: str,
    agent_name: str,
    msg: dict,
) -> None:
    """用收到的消息运行 teammate 一轮对话。"""
    teammate = _active_teammates.get(agent_name)
    if teammate is None:
        return

    ctx = teammate["ctx"]
    poller = teammate["poller"]
    agent_def = ctx.agent_def

    # 标记忙碌
    teammate["status"] = "running"
    poller.set_busy(True)

    try:
        from tools.subagent.runner import run_agent
        from tools import get_tools
        from tools.subagent.tools import resolve_agent_tools

        # 把消息作为新的 user 消息追加
        ctx.initial_messages.append({
            "role": "user",
            "content": f"[Message from {msg.get('from', 'leader')}]: {msg.get('text', '')}",
        })

        all_tools = get_tools()
        worker_tools = resolve_agent_tools(agent_def, all_tools)
        system_prompt = agent_def.resolve_system_prompt()

        async for _message in run_agent(ctx, worker_tools, system_prompt):
            pass

    except Exception as e:
        logger.exception("Teammate %s 轮次异常: %s", agent_name, e)
    finally:
        teammate["status"] = "idle"
        poller.set_busy(False)


# ---------------------------------------------------------------------------
# _shutdown_teammate — 关闭 teammate
# ---------------------------------------------------------------------------


async def _shutdown_teammate(agent_name: str) -> None:
    """优雅关闭 teammate。"""
    teammate = _active_teammates.get(agent_name)
    if teammate is None:
        return

    poller = teammate["poller"]
    await poller.stop()

    team_name = teammate["team_name"]

    # 从团队移除
    from tools.team.manager import remove_member
    remove_member(team_name, agent_name)

    teammate["status"] = "stopped"
    del _active_teammates[agent_name]

    logger.info("Teammate %s 已关闭", agent_name)


# ---------------------------------------------------------------------------
# get_active_teammates — 获取活跃 teammate 列表
# ---------------------------------------------------------------------------


def get_active_teammates() -> dict[str, dict[str, Any]]:
    """获取活跃 teammate 注册表。"""
    return dict(_active_teammates)


def get_teammate_status(agent_name: str) -> str | None:
    """获取 teammate 状态。"""
    teammate = _active_teammates.get(agent_name)
    if teammate is None:
        return None
    return teammate["status"]
