"""插件加载器 — 扫描目录、解析 manifest、按 kind 分类、去重。

扫描三个来源：
- 项目级：.agent/plugins/<name>/（从 cwd 向上到 home 每一级）
- 用户级：~/.agent/plugins/<name>/
- 内置：startup/plugins/bundled/<name>/（随项目分发，最低优先级）

同名插件高优先级覆盖低优先级（项目级 > 用户级 > 内置，深路径优先）。
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path

from startup.plugins.manifest import (
    LoadedPlugin,
    PluginManifest,
    parse_manifest,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

PLUGINS_DIR_NAME = "plugins"
AGENT_DIR_NAME = ".agent"
BUNDLED_PLUGINS_DIR = "bundled"  # 内置插件目录（随项目分发）


# ---------------------------------------------------------------------------
# _get_bundled_plugins_dir — 获取内置插件目录
# ---------------------------------------------------------------------------


def _get_bundled_plugins_dir() -> Path:
    """获取项目内置插件目录路径。

    内置插件随项目代码分发，位于 startup/plugins/bundled/。
    用户不需要手动安装，项目启动即可用。
    """
    # 本文件在 startup/plugins/loader.py，内置插件在 startup/plugins/bundled/
    return Path(__file__).parent / BUNDLED_PLUGINS_DIR


# ---------------------------------------------------------------------------
# get_plugin_dirs — 获取所有插件目录路径
# ---------------------------------------------------------------------------


def _get_plugin_dirs() -> list[Path]:
    """获取所有插件目录路径，按优先级从高到低排序。

    优先级：
    1. 项目级：从 cwd 向上到 home 的每一级 .agent/plugins（深路径优先）
    2. 用户级：~/.agent/plugins
    3. 内置：项目内的 startup/plugins/bundled/（最低优先级，被同名用户/项目插件覆盖）
    """
    dirs: list[Path] = []

    cwd = Path(os.getcwd()).resolve()
    home = Path(os.path.expanduser("~")).resolve()

    current = cwd
    while current is not None:
        plugin_dir = current / AGENT_DIR_NAME / PLUGINS_DIR_NAME
        dirs.append(plugin_dir)

        if current == home or current == current.parent:
            break
        current = current.parent

    # 用户级
    user_dir = home / AGENT_DIR_NAME / PLUGINS_DIR_NAME
    dirs.append(user_dir)

    # 内置（最低优先级）
    bundled_dir = _get_bundled_plugins_dir()
    dirs.append(bundled_dir)

    return dirs


# ---------------------------------------------------------------------------
# discover_plugins — 扫描所有插件
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def discover_plugins() -> tuple[LoadedPlugin, ...]:
    """扫描所有插件目录，返回已加载插件列表。

    解析每个插件目录的 plugin.json，构造 LoadedPlugin。
    同名插件项目级覆盖用户级。结果 memoize。

    Returns:
        LoadedPlugin 元组（不可变，配合 lru_cache）
    """
    plugins: list[LoadedPlugin] = []
    seen_names: set[str] = set()

    # 用户级插件目录，用于区分 user / project 来源
    user_dir = Path(os.path.expanduser("~")).resolve() / AGENT_DIR_NAME / PLUGINS_DIR_NAME

    for plugin_dir in _get_plugin_dirs():
        if not plugin_dir.is_dir():
            continue

        # 判断当前来源：bundled / user（~/.agent/plugins）/ project（其他 .agent/plugins）
        if plugin_dir == _get_bundled_plugins_dir():
            current_source = "bundled"
        elif plugin_dir == user_dir:
            current_source = "user"
        else:
            current_source = "project"

        # 遍历目录下的子目录（每个子目录是一个插件）
        for entry in sorted(plugin_dir.iterdir()):
            if not entry.is_dir():
                continue

            plugin_name = entry.name
            if plugin_name in seen_names:
                # 高优先级来源已加载同名插件，跳过
                continue

            manifest = parse_manifest(entry, source=current_source)
            if manifest is None:
                continue

            loaded = LoadedPlugin(manifest=manifest, enabled=True)
            plugins.append(loaded)
            seen_names.add(plugin_name)
            logger.debug("发现插件: %s (kind=%s, source=%s)", plugin_name, manifest.kind, manifest.source)

    return tuple(plugins)


# ---------------------------------------------------------------------------
# get_plugin_by_name — 按名称查找插件
# ---------------------------------------------------------------------------


def get_plugin_by_name(name: str) -> LoadedPlugin | None:
    """按名称查找已发现的插件。"""
    for plugin in discover_plugins():
        if plugin.manifest.name == name:
            return plugin
    return None


# ---------------------------------------------------------------------------
# get_plugins_by_kind — 按 kind 筛选插件
# ---------------------------------------------------------------------------


def get_plugins_by_kind(kind: str) -> list[LoadedPlugin]:
    """获取指定 kind 的所有插件。"""
    return [p for p in discover_plugins() if p.manifest.kind == kind]


# ---------------------------------------------------------------------------
# clear_cache — 清缓存
# ---------------------------------------------------------------------------


def clear_cache() -> None:
    """清除插件发现缓存。"""
    discover_plugins.cache_clear()
