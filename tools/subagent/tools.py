"""子代理工具池过滤。

根据 AgentDefinition 的 tools 白名单和 disallowed_tools 黑名单
过滤工具列表。general-purpose 通配符放行全部，Explore 移除写工具，
所有子代理移除 Agent 工具防递归。
"""

from __future__ import annotations

import logging

from tools.protocol import Tool

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# resolve_agent_tools — 按代理定义过滤工具池
# ---------------------------------------------------------------------------


def resolve_agent_tools(
    agent_def,
    all_tools: list[Tool],
) -> list[Tool]:
    """按代理定义过滤工具池。

    过滤规则（按优先级）：
    1. permission_mode 最小语义：read-only 只保留只读工具（继承父模式时无额外裁剪）
    2. disallowed_tools 黑名单中的工具一律移除
    3. tools 白名单：
       - None 或 ["*"] -> 全部放行（通配符）
       - 否则 -> 只保留白名单中的工具
    4. 所有子代理移除 Agent 工具（防止无限递归）

    MCP 工具（mcp__ 前缀）不受白名单限制，一律放行。

    Args:
        agent_def: 代理类型定义
        all_tools: 全部可用工具列表

    Returns:
        过滤后的工具列表
    """
    result: list[Tool] = []

    # permission_mode 最小语义：声明 read-only 的代理只保留只读工具
    perm = str(getattr(agent_def, "permission_mode", "") or "").lower().replace("-", "_")
    read_only_mode = perm in {"read_only", "readonly"}

    # 黑名单集合（始终移除；Agent 工具对所有子代理禁用防递归）
    disallowed = set(agent_def.disallowed_tools)
    disallowed.add("Agent")

    for tool in all_tools:
        # read-only 模式：按工具元数据裁剪（被拒时 executor 会说明只读边界）
        if read_only_mode and not tool.get_metadata().read_only and not tool.name.startswith("mcp__"):
            continue

        # MCP 工具不受白名单限制
        if tool.name.startswith("mcp__"):
            if tool.name in disallowed:
                continue
            result.append(tool)
            continue

        # 黑名单检查
        if tool.name in disallowed:
            continue

        # 白名单检查
        if agent_def.has_wildcard_tools():
            # 通配符 -> 全部放行
            result.append(tool)
        else:
            # 按白名单过滤
            if tool.name in (agent_def.tools or []):
                result.append(tool)

    logger.debug(
        "代理 %s 工具池: %d/%d 工具 (disallowed: %s)",
        agent_def.agent_type,
        len(result),
        len(all_tools),
        list(disallowed),
    )
    return result


# ---------------------------------------------------------------------------
# is_subagent_context — 判断是否在子代理上下文中
# ---------------------------------------------------------------------------


def is_subagent_context(context) -> bool:
    """判断当前是否在子代理执行上下文中。

    通过检查 ToolUseContext 的 tool_use_id 是否以 "agent_" 开头判断。
    主循环的 tool_use_id 为空或非 agent_ 前缀。
    """
    if context is None:
        return False
    tool_use_id = getattr(context, "tool_use_id", "")
    if not tool_use_id:
        return False
    return tool_use_id.startswith("agent_")
