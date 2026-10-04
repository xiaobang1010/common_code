"""插件代理源 — 从启用插件的 agents/*.md 加载代理定义（命名空间化）。

目录约定：插件目录下 `agents/<name>.md`，frontmatter 格式与用户级
自定义代理一致（复用 loader 的解析器）。代理名加载时转为
`<插件名>:<代理名>`，与内置/用户/项目代理互不冲突。
禁用（或未启用）的插件不提供代理。
"""

from __future__ import annotations

import logging
from pathlib import Path

from tools.subagent.types import AgentDefinition

logger = logging.getLogger(__name__)


def get_plugin_agent_definitions() -> list[AgentDefinition]:
    """汇总全部启用插件提供的代理定义。

    插件系统不可用（未初始化/异常）时返回空列表，不阻断派生。
    """
    try:
        from startup.plugins.manager import PluginManager

        plugins = PluginManager.get_enabled_plugins()
    except Exception as e:
        logger.debug("插件代理源不可用: %s", e)
        return []

    # 复用自定义代理的 frontmatter 解析（同包内部函数）
    from tools.subagent.loader import _parse_agent_file

    agents: list[AgentDefinition] = []
    for plugin in plugins:
        agents_dir = Path(plugin.manifest.path) / "agents"
        if not agents_dir.is_dir():
            continue
        for md_file in sorted(agents_dir.glob("*.md")):
            definition, diagnostic = _parse_agent_file(md_file, source="plugin")
            if diagnostic is not None:
                logger.warning(
                    "插件代理加载失败: %s (%s)",
                    diagnostic["file"],
                    diagnostic["message"],
                )
                continue
            # 命名空间化，避免与内置/用户/项目代理同名冲突
            definition.agent_type = f"{plugin.manifest.name}:{definition.agent_type}"
            definition.source = "plugin"
            agents.append(definition)
    return agents
