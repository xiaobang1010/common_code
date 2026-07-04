"""子代理上下文工厂。

为子代理创建隔离的执行上下文：克隆文件状态缓存、隔离消息历史、
共享模型客户端，设置 agent_id 和深度计数。

设计参考 Claude Code 的 createSubagentContext：
默认所有可变状态都隔离，仅显式 opt-in 共享特定回调。
"""

from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass, field
from typing import Any

from tools.protocol import ToolUseContext
from tools.subagent.types import AgentDefinition


# ---------------------------------------------------------------------------
# SubagentContext — 子代理执行上下文
# ---------------------------------------------------------------------------


@dataclass
class SubagentContext:
    """子代理执行上下文，持有子代理执行所需的全部隔离状态。

    Attributes:
        agent_id: 子代理唯一标识
        depth: 嵌套深度（主代理=0，子代理=1）
        agent_def: 代理类型定义
        tool_use_context: 隔离的工具执行上下文
        model: 解析后的模型名
        max_turns: 最大循环轮数
        is_async: 是否异步执行（后台运行）
        initial_messages: 子代理的初始消息列表（仅含 prompt）
    """

    agent_id: str
    depth: int
    agent_def: AgentDefinition
    tool_use_context: ToolUseContext
    model: str
    max_turns: int | None = None
    is_async: bool = False
    initial_messages: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# create_subagent_context — 创建隔离的子代理上下文
# ---------------------------------------------------------------------------


def create_subagent_context(
    parent_context: ToolUseContext | None,
    agent_def: AgentDefinition,
    main_loop_model: str,
    *,
    agent_id: str | None = None,
    depth: int = 1,
    is_async: bool = False,
    prompt: str = "",
) -> SubagentContext:
    """从父上下文创建隔离的子代理上下文。

    隔离策略：
    - messages: 全新列表（仅含 prompt 转换的 user 消息），不继承父对话
    - file_state_cache: 克隆父的缓存（子代理可独立修改不影响父）
    - abort_controller: 全新（子代理有独立的中断控制）
    - model: 按 agent_def 解析（inherit 则用主循环模型）

    Args:
        parent_context: 父代理的 ToolUseContext，None 表示无父上下文
        agent_def: 代理类型定义
        main_loop_model: 主循环模型名
        agent_id: 指定 agent_id，None 则自动生成
        depth: 嵌套深度
        is_async: 是否异步执行
        prompt: 子代理的任务指令

    Returns:
        SubagentContext 隔离上下文
    """
    # 生成 agent_id
    resolved_agent_id = agent_id or f"agent_{uuid.uuid4().hex[:8]}"

    # 解析模型
    model = agent_def.resolve_model(main_loop_model)

    # 克隆文件状态缓存（隔离可变状态）
    if parent_context is not None:
        file_state_cache = copy.deepcopy(parent_context.file_state_cache)
    else:
        file_state_cache = {}

    # 创建隔离的 ToolUseContext
    tool_use_context = ToolUseContext(
        permission_decision=None,
        messages=[],  # 全新消息列表
        file_state_cache=file_state_cache,
        abort_controller=None,  # 全新中断控制
        tool_use_id=resolved_agent_id,
    )

    # 构建初始消息（prompt 作为首轮 user 消息）
    initial_messages: list[dict] = []
    if prompt:
        initial_messages.append({"role": "user", "content": prompt})

    return SubagentContext(
        agent_id=resolved_agent_id,
        depth=depth,
        agent_def=agent_def,
        tool_use_context=tool_use_context,
        model=model,
        max_turns=agent_def.max_turns,
        is_async=is_async,
        initial_messages=initial_messages,
    )
