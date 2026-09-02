"""AgentDefinition — 子代理类型定义。

AgentDefinition 描述一种子代理的配置：用什么工具、什么系统提示词、
什么权限模式、什么模型。主 LLM 通过 Agent 工具派生子代理时，
根据 subagent_type 查找对应的 AgentDefinition。

设计参考 Claude Code 的 AgentDefinition：内置 general-purpose（全工具）
和 Explore（只读），支持自定义代理（.md 文件加载）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


# ---------------------------------------------------------------------------
# AgentDefinition — 子代理类型定义
# ---------------------------------------------------------------------------


@dataclass
class AgentDefinition:
    """子代理类型定义。

    Attributes:
        agent_type: 唯一标识，如 "general-purpose"、"Explore"
        when_to_use: 给主 LLM 看的"何时使用"描述
        tools: 工具白名单。None 或 ["*"] 表示全部工具
        disallowed_tools: 工具黑名单，从全部工具中移除这些
        model: 模型名。None 或 "inherit" 表示继承主循环模型
        permission_mode: 权限模式覆盖（如 "default" / "acceptEdits"）
        system_prompt: 系统提示词，或生成系统提示词的函数
        system_prompt_fn: 动态生成系统提示词的函数（优先于 system_prompt）
        max_turns: 最大循环轮数，None 表示不限
        token_budget: 累计 token 预算，None 表示沿用全局默认（0=不限）
        inject_agents_md: 是否把工作区 AGENTS.md 注入系统提示词
        background: 是否总是后台运行
        source: 来源标记（"built-in" / "user" / "project"）
    """

    agent_type: str
    when_to_use: str
    tools: list[str] | None = None
    disallowed_tools: list[str] = field(default_factory=list)
    model: str | None = None
    permission_mode: str | None = None
    system_prompt: str = ""
    system_prompt_fn: Callable[[], str] | None = None
    max_turns: int | None = None
    token_budget: int | None = None
    inject_agents_md: bool = True
    background: bool = False
    source: str = "built-in"

    # ------------------------------------------------------------------
    # 便捷方法
    # ------------------------------------------------------------------

    def resolve_system_prompt(self) -> str:
        """获取系统提示词。

        优先用 system_prompt_fn（可动态生成），否则返回静态 system_prompt。
        """
        if self.system_prompt_fn is not None:
            return self.system_prompt_fn()
        return self.system_prompt

    def has_wildcard_tools(self) -> bool:
        """工具白名单是否为通配符（全部放行）。"""
        return self.tools is None or self.tools == ["*"]

    def resolve_model(self, main_loop_model: str) -> str:
        """解析子代理使用的模型。

        解析链（优先级从高到低）：
        1. profile.model 显式指定（非 inherit）
        2. 全局配置 subagents.model_overrides[agent_type] 按类型覆盖
        3. 全局配置 subagents.default_model 统一默认
        4. 主循环模型
        配置读取失败时直接回退主循环模型，不阻断派生。
        """
        if self.model and self.model != "inherit":
            return self.model
        try:
            from startup.config import get_global_config

            sub_cfg = get_global_config().subagents
            override = sub_cfg.model_overrides.get(self.agent_type, "")
            if override:
                return override
            if sub_cfg.default_model:
                return sub_cfg.default_model
        except Exception:
            pass  # 配置系统未就绪时继承主循环模型
        return main_loop_model
