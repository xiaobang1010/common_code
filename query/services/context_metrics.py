"""上下文分类 token 估算。

把一次请求的上下文按来源拆成分类占比，供前端「上下文容量」面板展示。
估算口径与压缩触发共用 query.utils.tokens（字符数 ÷ 4），
保证面板数字和内部压缩判断一致。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from query.utils.tokens import estimate_tokens, estimate_tokens_for_messages
from query.utils.api import tool_to_api_schema

if TYPE_CHECKING:
    from prompts.system.sections import SystemPromptSection
    from tools.protocol import Tool

# 归入「技能」类的系统段名（其余系统段归「系统提示词」）
_SKILL_SECTION_NAMES = frozenset({"skill_guidance"})

# MCP 注册工具的名单前缀（插件注册的 MCP 工具按此归类）
_MCP_TOOL_PREFIX = "mcp__"


def build_context_breakdown(
    sections: list["SystemPromptSection"],
    tools: list["Tool"],
    history_messages: list[dict],
    skill_listing_text: str | None = None,
    recall_text: str | None = None,
) -> dict[str, int]:
    """构建当前请求上下文的分类 token 估算。

    Args:
        sections: 系统提示词段列表（含 name 与 content）
        tools: 本轮请求携带的工具列表
        history_messages: 会话历史消息（不含本轮易变注入，避免重复计数）
        skill_listing_text: 本轮注入的技能清单文本（归入 skills）
        recall_text: 本轮注入的记忆召回等提醒文本（归入 other）

    Returns:
        {分类名: 估算 token 数, "total": 各分类之和}；占比为 0 的分类不输出
    """
    buckets: dict[str, int] = {}

    # ---- 工具 schema：系统工具 / MCP 工具 ----
    for tool in tools or []:
        schema_text = json.dumps(tool_to_api_schema(tool), ensure_ascii=False)
        key = (
            "mcp_tools"
            if getattr(tool, "name", "").startswith(_MCP_TOOL_PREFIX)
            else "system_tools"
        )
        buckets[key] = buckets.get(key, 0) + estimate_tokens(schema_text)

    # ---- 系统提示词段：技能指导归 skills，其余归 system_prompt ----
    for section in sections or []:
        key = (
            "skills"
            if section.name in _SKILL_SECTION_NAMES
            else "system_prompt"
        )
        buckets[key] = buckets.get(key, 0) + estimate_tokens(section.content)

    # ---- 本轮易变注入 ----
    if skill_listing_text:
        buckets["skills"] = buckets.get("skills", 0) + estimate_tokens(skill_listing_text)
    if recall_text:
        buckets["other"] = buckets.get("other", 0) + estimate_tokens(recall_text)

    # ---- 会话历史 ----
    if history_messages:
        buckets["messages"] = buckets.get("messages", 0) + estimate_tokens_for_messages(
            history_messages
        )

    total = sum(buckets.values())
    return {**buckets, "total": total}
