"""子代理类型解析器 — 精确匹配 + 归一化模糊匹配，三态结构化结果。

解析策略（与主流客户端对齐）：
1. 精确匹配代理类型名
2. 归一化模糊匹配（NFKC + 小写 + 去空白与分隔符），唯一命中视为匹配
3. 多个候选 → ambiguous（附候选列表）；零命中 → not_found（附可用列表）

结构化错误以工具结果文本回给模型，让模型自我纠正而不是黑箱失败。
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field

from tools.subagent.types import AgentDefinition


# ---------------------------------------------------------------------------
# ResolveResult — 解析结果三态
# ---------------------------------------------------------------------------


@dataclass
class ResolveResult:
    """类型解析结果。

    Attributes:
        kind: "matched" / "ambiguous" / "not_found"
        agent: 命中的代理定义（仅 matched）
        matches: 候选类型名（仅 ambiguous）
        available: 全部可用类型名（仅 not_found）
    """

    kind: str
    agent: AgentDefinition | None = None
    matches: list[str] = field(default_factory=list)
    available: list[str] = field(default_factory=list)

    def error_text(self, requested: str) -> str:
        """生成给模型的结构化错误文本（非 matched 时调用）。"""
        if self.kind == "ambiguous":
            options = " or ".join(self.matches)
            return (
                f"Agent type '{requested}' is ambiguous — matches "
                f"{', '.join(self.matches)}. Use the exact name: {options}."
            )
        available = ", ".join(self.available)
        return (
            f"Agent type '{requested}' not found. Available agents: {available}."
        )


# ---------------------------------------------------------------------------
# 归一化与解析
# ---------------------------------------------------------------------------


def normalize_agent_name(name: str) -> str:
    """归一化代理类型名：NFKC + 小写 + 去空白与常见分隔符。"""
    normalized = unicodedata.normalize("NFKC", name.strip()).lower()
    return "".join(ch for ch in normalized if ch.isalnum())


def get_all_agent_definitions() -> list[AgentDefinition]:
    """汇总全部代理定义：内置 + 插件 + 自定义（用户级覆盖项目级）。

    插件代理源由第六组任务接入，此处预留合并点。
    """
    from tools.subagent.built_in_agents import get_built_in_agents
    from tools.subagent.loader import load_custom_agents

    agents: list[AgentDefinition] = list(get_built_in_agents())
    agents.extend(_get_plugin_agents())
    custom, _diagnostics = load_custom_agents()
    agents.extend(custom)
    return agents


def _get_plugin_agents() -> list[AgentDefinition]:
    """插件提供的代理定义（未接入插件代理源时为空列表）。"""
    try:
        from tools.subagent.plugin_agents import get_plugin_agent_definitions

        return get_plugin_agent_definitions()
    except ImportError:
        return []


def resolve_agent_type(agent_type: str) -> ResolveResult:
    """按类型名解析代理定义，三态结构化结果。"""
    agents = get_all_agent_definitions()

    # 1. 精确匹配
    for agent in agents:
        if agent.agent_type == agent_type:
            return ResolveResult(kind="matched", agent=agent)

    # 2. 归一化模糊匹配
    wanted = normalize_agent_name(agent_type)
    if wanted:
        fuzzy = [a for a in agents if normalize_agent_name(a.agent_type) == wanted]
        if len(fuzzy) == 1:
            return ResolveResult(kind="matched", agent=fuzzy[0])
        if len(fuzzy) > 1:
            return ResolveResult(
                kind="ambiguous",
                matches=[a.agent_type for a in fuzzy],
            )

    # 3. 未命中
    return ResolveResult(
        kind="not_found",
        available=[a.agent_type for a in agents],
    )
