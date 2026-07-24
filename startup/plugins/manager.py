"""PluginManager — 插件启用/禁用管理。

管理插件的启用/禁用状态，配置持久化到 ~/.agent/config.json 的 plugins 字段。
禁用的插件不加载其任何能力。

配置格式：
    {
        "plugins": {
            "enabled": ["my-plugin", "another-plugin"],
            "disabled": ["bad-plugin"]
        }
    }
"""

from __future__ import annotations

import logging
from typing import Any

from startup.plugins.loader import discover_plugins, get_plugin_by_name, clear_cache
from startup.plugins.manifest import LoadedPlugin

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 配置读写辅助
# ---------------------------------------------------------------------------


def _read_plugins_config() -> dict[str, Any]:
    """从 config.json 读取 plugins 配置段。"""
    try:
        from startup.config import get_global_config
        config = get_global_config()
        # GlobalConfig 可能有 plugins 字段，也可能没有，用 raw dict 读
        import json
        from startup.config import get_global_config_path
        path = get_global_config_path()
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            return data.get("plugins", {})
    except Exception:
        pass
    return {}


def _write_plugins_config(plugins_config: dict[str, Any]) -> None:
    """写入 plugins 配置段到 config.json。"""
    try:
        import json
        from startup.config import get_global_config_path, _config_lock
        path = get_global_config_path()
        with _config_lock:
            data = {}
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
            data["plugins"] = plugins_config
            path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        logger.warning("写入 plugins 配置失败: %s", e)


def _get_enabled_set() -> set[str]:
    """获取启用插件名集合。"""
    cfg = _read_plugins_config()
    return set(cfg.get("enabled", []))


def _get_disabled_set() -> set[str]:
    """获取禁用插件名集合。"""
    cfg = _read_plugins_config()
    return set(cfg.get("disabled", []))


# ---------------------------------------------------------------------------
# PluginManager — 启用/禁用管理
# ---------------------------------------------------------------------------


class PluginManager:
    """插件启用/禁用管理器。

    启用逻辑：
    - 已安装插件默认启用（除非在 disabled 列表中）
    - enabled 列表用于显式启用（如第三方插件需 opt-in）
    - disabled 列表优先级最高（永不加载）
    """

    @staticmethod
    def get_all_plugins() -> list[LoadedPlugin]:
        """获取所有已发现插件，标记启用/禁用状态。"""
        disabled = _get_disabled_set()
        enabled = _get_enabled_set()
        result: list[LoadedPlugin] = []

        for plugin in discover_plugins():
            name = plugin.manifest.name
            # disabled 列表优先
            if name in disabled:
                plugin.enabled = False
            else:
                plugin.enabled = True
            result.append(plugin)

        return result

    @staticmethod
    def get_enabled_plugins() -> list[LoadedPlugin]:
        """获取所有已启用的插件。"""
        return [p for p in PluginManager.get_all_plugins() if p.enabled]

    @staticmethod
    def get_enabled_by_kind(kind: str) -> list[LoadedPlugin]:
        """获取指定 kind 的已启用插件。"""
        return [p for p in PluginManager.get_enabled_plugins() if p.manifest.kind == kind]

    @staticmethod
    def enable_plugin(name: str) -> bool:
        """启用插件。

        Returns:
            True 成功，False 插件不存在
        """
        plugin = get_plugin_by_name(name)
        if plugin is None:
            return False

        cfg = _read_plugins_config()
        enabled = set(cfg.get("enabled", []))
        disabled = set(cfg.get("disabled", []))

        enabled.add(name)
        disabled.discard(name)

        cfg["enabled"] = sorted(enabled)
        cfg["disabled"] = sorted(disabled)
        _write_plugins_config(cfg)

        clear_cache()
        logger.info("插件启用: %s", name)
        return True

    @staticmethod
    def disable_plugin(name: str) -> bool:
        """禁用插件。

        Returns:
            True 成功，False 插件不存在
        """
        plugin = get_plugin_by_name(name)
        if plugin is None:
            return False

        cfg = _read_plugins_config()
        enabled = set(cfg.get("enabled", []))
        disabled = set(cfg.get("disabled", []))

        enabled.discard(name)
        disabled.add(name)

        cfg["enabled"] = sorted(enabled)
        cfg["disabled"] = sorted(disabled)
        _write_plugins_config(cfg)

        clear_cache()
        logger.info("插件禁用: %s", name)
        return True

    @staticmethod
    def is_enabled(name: str) -> bool:
        """检查插件是否启用。"""
        for plugin in PluginManager.get_all_plugins():
            if plugin.manifest.name == name:
                return plugin.enabled
        return False
