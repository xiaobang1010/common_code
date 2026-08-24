"""工具注册表 — 动态工具池。

合并内置工具 + Skill + Agent + Task 工具族 + SendMessage + TeamCreate，
支持上下文过滤参数（按代理类型/权限过滤）。
"""

from __future__ import annotations

import logging
from typing import Any

from tools.protocol import Tool

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 内置工具导入
# ---------------------------------------------------------------------------

from tools.implementations.bash_tool import get_bash_tool
from tools.implementations.file_read_tool import get_file_read_tool
from tools.implementations.file_edit_tool import get_file_edit_tool
from tools.implementations.file_write_tool import get_file_write_tool
from tools.implementations.glob_tool import get_glob_tool
from tools.implementations.grep_tool import get_grep_tool


# ---------------------------------------------------------------------------
# 上下文过滤器
# ---------------------------------------------------------------------------


class ToolContextFilter:
    """工具上下文过滤器，按代理类型/角色过滤工具。

    Attributes:
        is_subagent: 是否子代理上下文（移除 Agent 工具防递归）
        is_teammate: 是否 teammate 上下文（移除 TeamCreate）
        agent_type: 子代理类型（如 "Explore" 移除写工具）
    """

    def __init__(
        self,
        is_subagent: bool = False,
        is_teammate: bool = False,
        agent_type: str | None = None,
    ) -> None:
        self.is_subagent = is_subagent
        self.is_teammate = is_teammate
        self.agent_type = agent_type

    @classmethod
    def main_loop(cls) -> "ToolContextFilter":
        """主循环过滤器（全部工具）。"""
        return cls()

    @classmethod
    def for_subagent(cls, agent_type: str) -> "ToolContextFilter":
        """子代理过滤器。"""
        return cls(is_subagent=True, agent_type=agent_type)


# ---------------------------------------------------------------------------
# get_tools — 获取动态工具池
# ---------------------------------------------------------------------------


def get_tools(context_filter: ToolContextFilter | None = None) -> list[Tool]:
    """返回动态工具池。

    合并内置工具 + Skill + Agent + Task 工具族 + SendMessage + TeamCreate。
    根据 context_filter 过滤（子代理移除 Agent，teammate 移除 TeamCreate）。

    Args:
        context_filter: 上下文过滤器，None 表示主循环（全部工具）

    Returns:
        过滤后的工具列表
    """
    tools: list[Tool] = []

    # 1. 内置 6 个工具
    tools.extend([
        get_bash_tool(),
        get_file_read_tool(),
        get_file_edit_tool(),
        get_file_write_tool(),
        get_glob_tool(),
        get_grep_tool(),
    ])

    # 2. Skill 工具
    try:
        from tools.skills.skill_tool import get_skill_tool
        tools.append(get_skill_tool())
    except ImportError:
        pass

    # 3. Agent 工具（子代理上下文跳过，防递归）
    if context_filter is None or not context_filter.is_subagent:
        try:
            from tools.subagent.agent_tool import get_agent_tool
            tools.append(get_agent_tool())
        except ImportError:
            pass

    # 4. Task 工具族
    try:
        from tools.team.task_tools import get_all_task_tools
        tools.extend(get_all_task_tools())
    except ImportError:
        pass

    # 5. SendMessage 工具
    #    主循环注册 subagent 的 SendMessage（续接子代理）
    #    teammate 上下文注册 team 的 SendMessage（teammate 间通信）
    if context_filter is not None and context_filter.is_teammate:
        # teammate 上下文：team 邮箱通信
        try:
            from tools.team.send_message_tool import get_send_message_tool
            tools.append(get_send_message_tool())
        except ImportError:
            pass
    else:
        # 主循环：subagent 续接
        try:
            from tools.subagent.send_message import get_send_message_tool
            tools.append(get_send_message_tool())
        except ImportError:
            pass

    # 5.5 子代理任务管理工具（主循环/子代理均可管理后台任务）
    try:
        from tools.subagent.task_tools import (
            get_get_subagent_output_tool,
            get_stop_subagent_tool,
        )
        tools.append(get_get_subagent_output_tool())
        tools.append(get_stop_subagent_tool())
    except ImportError:
        pass

    # 6. TeamCreate 工具（teammate 上下文跳过，不能建子团队）
    if context_filter is None or not context_filter.is_teammate:
        try:
            from tools.team.team_create_tool import get_team_create_tool
            tools.append(get_team_create_tool())
        except ImportError:
            pass

    # 7. SummarizeTeam 工具（leader 用于综合 teammate 结果）
    try:
        from tools.team.summarize import get_summarize_team_tool
        tools.append(get_summarize_team_tool())
    except ImportError:
        pass

    # 8. 记忆工具（memory-palace 插件激活时注册）
    try:
        from memory.plugin.tools import get_memory_tools
        memory_tools = get_memory_tools()
        tools.extend(memory_tools)
    except ImportError:
        pass

    # 9. AskUserQuestion 工具（仅主循环可用，子代理/teammate 不能向用户提问）
    if context_filter is None or (not context_filter.is_subagent and not context_filter.is_teammate):
        try:
            from tools.implementations.ask_user_question import get_ask_user_question_tool
            tools.append(get_ask_user_question_tool())
        except ImportError:
            pass

    # 7. 上下文过滤：子代理按代理定义过滤工具
    if context_filter is not None and context_filter.is_subagent:
        tools = _filter_for_subagent(tools, context_filter)

    return tools


# ---------------------------------------------------------------------------
# _filter_for_subagent — 子代理工具过滤
# ---------------------------------------------------------------------------


def _filter_for_subagent(
    tools: list[Tool],
    context_filter: ToolContextFilter,
) -> list[Tool]:
    """按子代理类型过滤工具。

    Explore 代理移除写工具，所有子代理移除 Agent 工具。
    """
    from tools.subagent.built_in_agents import find_agent_by_type
    from tools.subagent.tools import resolve_agent_tools

    agent_def = find_agent_by_type(context_filter.agent_type or "general-purpose")
    if agent_def is None:
        # 找不到代理定义，至少移除 Agent 工具
        return [t for t in tools if t.name != "Agent"]

    return resolve_agent_tools(agent_def, tools)
