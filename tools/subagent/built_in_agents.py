"""内置子代理类型定义。

注册两种内置代理：
- general-purpose：全工具，通用研究/搜索/多步骤任务
- Explore：只读，快速搜索代码库，禁止任何文件修改

自定义代理（.md 文件加载）在后续阶段实现。
"""

from __future__ import annotations

from tools.subagent.types import AgentDefinition


# ---------------------------------------------------------------------------
# general-purpose 代理系统提示词
# ---------------------------------------------------------------------------

_GENERAL_PURPOSE_PROMPT = """\
你是一个通用研究助手。你可以使用所有可用工具来完成复杂的研究、搜索和多步骤任务。

工作方式：
- 仔细阅读任务描述，理解需要完成什么
- 使用工具（Read、Glob、Grep、Bash 等）收集信息
- 完成任务后，简洁地汇报你的发现和结果
- 不要重复用户已经知道的信息
- 如果任务无法完成，说明原因
"""


# ---------------------------------------------------------------------------
# Explore 代理系统提示词
# ---------------------------------------------------------------------------

_EXPLORE_PROMPT = """\
你是一个只读搜索代理。你的任务是快速搜索和浏览代码库，找到相关信息。

限制：
- 你不能修改任何文件（Write、Edit、Bash 不可用）
- 你只能使用 Read、Glob、Grep 等读工具
- 专注于高效地定位信息

工作方式：
- 先用 Glob/Grep 搜索关键词定位文件
- 再用 Read 读取相关文件的内容
- 汇报时给出文件路径和关键发现，不要大段粘贴代码
- 只返回结论，不需要文件转储
"""


# ---------------------------------------------------------------------------
# 所有子代理默认禁用的工具（防止递归等问题）
# ---------------------------------------------------------------------------

# Agent 工具对所有子代理禁用，防止无限递归
ALL_AGENT_DISALLOWED_TOOLS: list[str] = ["Agent"]

# Explore 代理额外禁用的写工具
EXPLORE_DISALLOWED_TOOLS: list[str] = [
    "Write",
    "Edit",
    "Bash",
    "MultiEdit",
]


# ---------------------------------------------------------------------------
# 内置代理定义
# ---------------------------------------------------------------------------


def _general_purpose_agent() -> AgentDefinition:
    """general-purpose 代理：全工具，通用任务。"""
    return AgentDefinition(
        agent_type="general-purpose",
        when_to_use="通用研究和多步骤任务，需要读写文件或执行命令",
        tools=None,  # None = 全部工具
        disallowed_tools=list(ALL_AGENT_DISALLOWED_TOOLS),
        system_prompt=_GENERAL_PURPOSE_PROMPT,
        source="built-in",
    )


def _explore_agent() -> AgentDefinition:
    """Explore 代理：只读，快速搜索。"""
    return AgentDefinition(
        agent_type="Explore",
        when_to_use="只读搜索任务，需要在代码库中快速定位信息",
        tools=None,  # 全部工具，但通过 disallowed_tools 移除写工具
        disallowed_tools=list(ALL_AGENT_DISALLOWED_TOOLS) + list(EXPLORE_DISALLOWED_TOOLS),
        model="inherit",
        system_prompt=_EXPLORE_PROMPT,
        omit_user_context=True,  # 只读代理省略用户上下文，省 token
        source="built-in",
    )


# ---------------------------------------------------------------------------
# 注册表
# ---------------------------------------------------------------------------


def get_built_in_agents() -> list[AgentDefinition]:
    """获取所有内置代理定义。"""
    return [
        _general_purpose_agent(),
        _explore_agent(),
    ]


def find_agent_by_type(agent_type: str) -> AgentDefinition | None:
    """按 agent_type 查找代理定义。

    查找顺序：内置代理 → 自定义代理（后续阶段实现）。
    """
    for agent in get_built_in_agents():
        if agent.agent_type == agent_type:
            return agent

    # 自定义代理（后续阶段实现）
    # from tools.subagent.loader import find_custom_agent
    # return find_custom_agent(agent_type)

    return None


def get_agent_listing() -> list[dict[str, str]]:
    """获取代理类型列表，用于注入系统提示词。

    返回 [{"type": ..., "when_to_use": ..., "tools": ...}, ...]
    """
    listing: list[dict[str, str]] = []
    for agent in get_built_in_agents():
        tools_desc = "all" if agent.has_wildcard_tools() else ", ".join(agent.tools or [])
        if agent.disallowed_tools:
            tools_desc += f" (disallowed: {', '.join(agent.disallowed_tools)})"
        listing.append({
            "type": agent.agent_type,
            "when_to_use": agent.when_to_use,
            "tools": tools_desc,
        })
    return listing
