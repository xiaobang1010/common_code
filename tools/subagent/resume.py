"""子代理 resume — 从 transcript 恢复上下文，后台继续运行。

当 SendMessage 到已停止的子代理时，从磁盘 transcript 加载历史消息，
追加新 prompt，注册为后台任务异步运行（SendMessage 不再同步阻塞父轮次），
完成后经统一通知管线投递父会话。终态由生命周期驱动任务按实际结果记录
（completed/failed/stopped），不再无条件记 completed。

任务注册与状态查询统一走 tools/subagent/registry.py 的 SubagentTaskRegistry。
"""

from __future__ import annotations

import logging

from tools.protocol import ToolUseContext
from tools.subagent.context import create_subagent_context
from tools.subagent.transcript import get_agent_transcript, read_agent_metadata

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# resume_agent_background — 从 transcript 恢复（后台运行）
# ---------------------------------------------------------------------------


async def resume_agent_background(
    agent_id: str,
    prompt: str,
    parent_context: ToolUseContext | None = None,
) -> str:
    """从 transcript 恢复子代理，追加新 prompt，后台继续运行。

    流程：
    1. 加载历史消息与元数据
    2. 查找代理定义（找不到降级 general-purpose）
    3. 创建隔离上下文（历史消息 + 新 prompt），应用预算默认
    4. 子会话 upsert（重入同一 agent_id 复用旧会话行）
    5. 注册后台任务并启动驱动任务（立即返回，完成时通知）

    Args:
        agent_id: 子代理 ID
        prompt: 新的任务指令
        parent_context: 调用方上下文（父会话标识，通知投递用）

    Returns:
        给模型的说明文本（后台已启动）
    """
    # 1. 加载历史消息
    resumed_messages = get_agent_transcript(agent_id)
    if resumed_messages is None:
        raise RuntimeError(f"No transcript found for agent: {agent_id}")

    # 2. 读取元数据与代理定义
    meta = read_agent_metadata(agent_id)
    agent_type = meta.get("agent_type", "general-purpose") if meta else "general-purpose"

    from tools.subagent.built_in_agents import find_agent_by_type

    agent_def = find_agent_by_type(agent_type)
    if agent_def is None:
        # 找不到原始类型，降级为 general-purpose
        agent_def = find_agent_by_type("general-purpose")

    # 3. 创建上下文（历史消息 + 新 prompt；resume 一律后台）
    from query.services.api.client import get_default_model

    ctx = create_subagent_context(
        parent_context=parent_context,
        agent_def=agent_def,
        main_loop_model=get_default_model(),
        agent_id=agent_id,
        is_async=True,
        prompt="",  # prompt 单独追加在历史之后
    )
    ctx.initial_messages = resumed_messages + [{"role": "user", "content": prompt}]

    # 预算默认与其他派生路径一致
    from tools.subagent.lifecycle import _apply_budget_defaults

    _apply_budget_defaults(ctx, agent_def)

    # 4. 子会话 upsert（重入场景复用旧会话行，不产生重复）
    parent_session_id = (
        parent_context.session_id if parent_context is not None else ""
    ) or None
    try:
        from server.paths import effective_root

        workspace_path = effective_root()
    except Exception:
        workspace_path = ""
    try:
        from tools.subagent.session_binding import ensure_child_session
        from tools.subagent.registry import MODE_BACKGROUND

        ctx.child_session_id = (
            ensure_child_session(
                agent_id,
                parent_session_id=parent_session_id,
                workspace_path=workspace_path,
                title=f"resume: {prompt[:40]}",
                agent_type=agent_def.agent_type,
                mode=MODE_BACKGROUND,
            )
            or ""
        )
    except Exception as e:
        logger.warning("resume 子会话绑定失败（降级）: %s", e)
        ctx.child_session_id = ""

    # 5. 工具池与系统提示词，注册后台任务并启动驱动
    from tools import get_tools
    from tools.subagent.context import build_subagent_system_prompt
    from tools.subagent.lifecycle import launch_background_subagent
    from tools.subagent.tools import resolve_agent_tools

    all_tools = get_tools()
    worker_tools = resolve_agent_tools(agent_def, all_tools)
    system_prompt = build_subagent_system_prompt(agent_def)

    task = launch_background_subagent(
        ctx,
        worker_tools,
        system_prompt,
        description=f"resume: {prompt[:40]}",
        parent_session_id=parent_session_id,
    )

    logger.info("子代理 %s 已从 transcript 后台恢复 (parent=%s)", agent_id, parent_session_id)
    return (
        f"Agent \"{agent_id}\" resumed in the background with your message. "
        f"You'll be notified when it finishes."
    )
