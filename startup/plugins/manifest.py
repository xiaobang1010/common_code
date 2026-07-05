"""插件 manifest 解析与校验。

插件目录约定格式：
    my-plugin/
    ├── .agent-plugin/
    │   └── plugin.json     # manifest（必填）
    ├── skills/             # 技能目录（可选）
    ├── commands/           # slash 命令（可选）
    ├── hooks/
    │   └── hooks.json      # hooks 配置（可选）
    ├── .mcp.json           # MCP 服务器配置（可选）
    └── memory.py           # 记忆后端实现（memory kind 必填）

plugin.json 格式：
    {
        "name": "my-plugin",
        "version": "1.0.0",
        "description": "插件描述",
        "author": "作者",
        "kind": "standard",          // standard / llm-provider / memory
        "dependencies": [],           // 依赖的其他插件名
        "llm_provider": {             // llm-provider kind 必填
            "base_url": "...",
            "api_key": "...",
            "model": "...",
            "default_model": "..."
        }
    }
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

MANIFEST_DIR = ".agent-plugin"
MANIFEST_FILE = "plugin.json"

# 插件 kind 类型
KIND_STANDARD = "standard"
KIND_LLM_PROVIDER = "llm-provider"
KIND_MEMORY = "memory"

VALID_KINDS = {KIND_STANDARD, KIND_LLM_PROVIDER, KIND_MEMORY}

# manifest 必填字段
REQUIRED_FIELDS = {"name", "version"}


# ---------------------------------------------------------------------------
# LLMProviderConfig — LLM 供应商配置
# ---------------------------------------------------------------------------


@dataclass
class LLMProviderConfig:
    """LLM 供应商配置（llm-provider kind 插件提供）。

    Attributes:
        name: 供应商名（等于插件名）
        base_url: API base URL
        api_key: API key（可能为空，由环境变量补充）
        model: 默认模型名
        models: 可用模型列表（可选）
    """

    name: str
    base_url: str
    api_key: str = ""
    model: str = ""
    models: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# PluginManifest — 插件清单
# ---------------------------------------------------------------------------


@dataclass
class PluginManifest:
    """插件清单，从 plugin.json 解析。

    Attributes:
        name: 插件名（唯一标识）
        version: 版本号
        description: 描述
        author: 作者
        kind: 插件类型（standard / llm-provider / memory）
        dependencies: 依赖的其他插件名列表
        llm_provider: LLM 供应商配置（kind=llm-provider 时有值）
        path: 插件目录的绝对路径
        source: 来源标记（"user" / "project"）
    """

    name: str
    version: str
    description: str = ""
    author: str = ""
    kind: str = KIND_STANDARD
    dependencies: list[str] = field(default_factory=list)
    llm_provider: LLMProviderConfig | None = None
    path: str = ""
    source: str = "user"

    # ------------------------------------------------------------------
    # 便捷方法
    # ------------------------------------------------------------------

    def is_standard(self) -> bool:
        """是否为 standard kind。"""
        return self.kind == KIND_STANDARD

    def is_llm_provider(self) -> bool:
        """是否为 llm-provider kind。"""
        return self.kind == KIND_LLM_PROVIDER

    def is_memory(self) -> bool:
        """是否为 memory kind。"""
        return self.kind == KIND_MEMORY


# ---------------------------------------------------------------------------
# LoadedPlugin — 已加载的插件
# ---------------------------------------------------------------------------


@dataclass
class LoadedPlugin:
    """已加载的插件运行时状态。

    Attributes:
        manifest: 插件清单
        enabled: 是否启用
        skills_registered: 实际注册的技能名列表
        hooks_registered: 实际注册的 hook 类型列表
        commands_registered: 实际注册的命令名列表
        mcp_servers_registered: 实际注册的 MCP 服务器名列表
        error: 加载错误信息（None 表示无错误）
    """

    manifest: PluginManifest
    enabled: bool = True
    skills_registered: list[str] = field(default_factory=list)
    hooks_registered: list[str] = field(default_factory=list)
    commands_registered: list[str] = field(default_factory=list)
    mcp_servers_registered: list[str] = field(default_factory=list)
    error: str | None = None


# ---------------------------------------------------------------------------
# parse_manifest — 解析 plugin.json
# ---------------------------------------------------------------------------


def parse_manifest(plugin_dir: Path, source: str = "user") -> PluginManifest | None:
    """解析插件目录下的 plugin.json，构造 PluginManifest。

    Args:
        plugin_dir: 插件目录路径
        source: 来源标记（"user" / "project" / "bundled"），由 loader 传入

    Returns:
        PluginManifest 或 None（解析失败时返回 None 并记录 warning）
    """
    manifest_path = plugin_dir / MANIFEST_DIR / MANIFEST_FILE

    if not manifest_path.is_file():
        logger.warning("插件目录缺少 manifest: %s", plugin_dir)
        return None

    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("manifest 解析失败 %s: %s", manifest_path, e)
        return None

    return _build_manifest(data, str(plugin_dir), source)


# ---------------------------------------------------------------------------
# _build_manifest — 从字典构造 PluginManifest
# ---------------------------------------------------------------------------


def _build_manifest(data: dict[str, Any], plugin_path: str, source: str = "user") -> PluginManifest | None:
    """从 manifest 字典构造 PluginManifest，校验必填字段。"""
    # 校验必填字段
    for field_name in REQUIRED_FIELDS:
        if not data.get(field_name):
            logger.warning("manifest 缺少必填字段 '%s': %s", field_name, plugin_path)
            return None

    # 校验 kind
    kind = data.get("kind", KIND_STANDARD)
    if kind not in VALID_KINDS:
        logger.warning("manifest kind '%s' 无效，有效值: %s: %s", kind, VALID_KINDS, plugin_path)
        return None

    # 解析 llm_provider 配置
    llm_provider = None
    if kind == KIND_LLM_PROVIDER:
        provider_data = data.get("llm_provider")
        if not provider_data or not isinstance(provider_data, dict):
            logger.warning("llm-provider 插件缺少 llm_provider 配置: %s", plugin_path)
            return None
        if not provider_data.get("base_url"):
            logger.warning("llm-provider 配置缺少 base_url: %s", plugin_path)
            return None
        llm_provider = LLMProviderConfig(
            name=data["name"],
            base_url=provider_data["base_url"],
            api_key=provider_data.get("api_key", ""),
            model=provider_data.get("model", ""),
            models=provider_data.get("models", []),
        )

    # 来源标记由 loader 传入（user / project / bundled），直接信任，不做二次推断
    return PluginManifest(
        name=data["name"],
        version=data["version"],
        description=data.get("description", ""),
        author=data.get("author", ""),
        kind=kind,
        dependencies=data.get("dependencies", []),
        llm_provider=llm_provider,
        path=plugin_path,
        source=source,
    )
