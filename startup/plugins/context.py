"""PluginContext — 插件注册门面。

给插件提供的注册接口，插件通过 context 声明自己提供了什么能力。
PluginContext 记录插件实际注册了什么，供 PluginManager 统计。

注意：当前采用"声明式加载"——standard kind 的插件能力（skills/hooks/MCP）
由 loader 自动检测子目录加载，不需要插件主动调 register。
PluginContext 主要用于 llm-provider 和 memory kind 的显式注册。
"""

from __future__ import annotations

import logging
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from startup.plugins.manifest import PluginManifest

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# PluginContext — 插件注册门面
# ---------------------------------------------------------------------------


class PluginContext:
    """插件注册门面，插件通过它声明自己提供的能力。

    Attributes:
        manifest: 插件清单
        skills: 已注册的技能名列表
        hooks: 已注册的 hook 类型列表
        commands: 已注册的命令名列表
        mcp_servers: 已注册的 MCP 服务器名列表
        llm_provider: 已注册的 LLM 供应商配置（llm-provider kind）
        memory_provider: 已注册的记忆后端工厂函数（memory kind）
    """

    def __init__(self, manifest: "PluginManifest") -> None:
        self.manifest = manifest
        self.skills: list[str] = []
        self.hooks: list[str] = []
        self.commands: list[str] = []
        self.mcp_servers: list[str] = []
        self.llm_provider: Any | None = None
        self.memory_provider: Any | None = None

    # ------------------------------------------------------------------
    # 注册方法
    # ------------------------------------------------------------------

    def register_skill(self, name: str) -> None:
        """声明插件提供了一个技能。"""
        self.skills.append(name)

    def register_hook(self, hook_type: str) -> None:
        """声明插件注册了一个 hook。"""
        self.hooks.append(hook_type)

    def register_command(self, name: str) -> None:
        """声明插件提供了一个 slash 命令。"""
        self.commands.append(name)

    def register_mcp_server(self, name: str) -> None:
        """声明插件提供了一个 MCP 服务器。"""
        self.mcp_servers.append(name)

    def register_llm_provider(self, config: Any) -> None:
        """注册 LLM 供应商配置（llm-provider kind）。"""
        self.llm_provider = config

    def register_memory_provider(self, factory: Any) -> None:
        """注册记忆后端工厂函数（memory kind）。

        factory 是一个 callable，调用后返回 MemoryProvider 实例。
        """
        self.memory_provider = factory

    # ------------------------------------------------------------------
    # 统计
    # ------------------------------------------------------------------

    def total_registered(self) -> int:
        """返回注册的能力总数。"""
        return (
            len(self.skills)
            + len(self.hooks)
            + len(self.commands)
            + len(self.mcp_servers)
            + (1 if self.llm_provider is not None else 0)
            + (1 if self.memory_provider is not None else 0)
        )
