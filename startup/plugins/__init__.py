"""插件系统 - 约定优先的插件容器，管理三类插件：standard/llm-provider/memory。

公共接口：
    discover_plugins() 等 loader 模块函数: 扫描目录、解析 manifest、按 kind 分类
    PluginManager: 启用/禁用管理
    PluginManifest: 插件清单数据结构
    init_plugins(): 启动时初始化入口
"""

from __future__ import annotations

import logging

from startup.plugins.manifest import (
    KIND_LLM_PROVIDER,
    KIND_MEMORY,
    KIND_STANDARD,
    LoadedPlugin,
    LLMProviderConfig,
    PluginManifest,
)
from startup.plugins.loader import (
    clear_cache,
    discover_plugins,
    get_plugin_by_name,
    get_plugins_by_kind,
)
from startup.plugins.manager import PluginManager

logger = logging.getLogger(__name__)

# 标记是否已初始化
_initialized = False


# ---------------------------------------------------------------------------
# init_plugins — 启动时初始化
# ---------------------------------------------------------------------------


def init_plugins() -> None:
    """启动时初始化插件系统。

    扫描所有插件目录，加载已启用的插件。
    幂等：已初始化则跳过。
    """
    global _initialized
    if _initialized:
        return
    _initialized = True

    plugins = discover_plugins()
    enabled = PluginManager.get_enabled_plugins()

    logger.info(
        "插件系统初始化：发现 %d 个插件，启用 %d 个",
        len(plugins),
        len(enabled),
    )

    for plugin in enabled:
        logger.debug(
            "已加载插件: %s v%s (kind=%s)",
            plugin.manifest.name,
            plugin.manifest.version,
            plugin.manifest.kind,
        )


# ---------------------------------------------------------------------------
# 公共导出
# ---------------------------------------------------------------------------

__all__ = [
    # 常量
    "KIND_STANDARD",
    "KIND_LLM_PROVIDER",
    "KIND_MEMORY",
    # 数据结构
    "PluginManifest",
    "LoadedPlugin",
    "LLMProviderConfig",
    # 加载器
    "discover_plugins",
    "get_plugin_by_name",
    "get_plugins_by_kind",
    "clear_cache",
    # 管理器
    "PluginManager",
    # 初始化
    "init_plugins",
]
