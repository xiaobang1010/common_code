"""MCP 配置解析 — 参考原始 config.ts。

从 settings.json 的 mcpServers 字段读取 MCP 服务器配置，
解析为 MCPServerConfig 字典。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from query.services.mcp.types import MCPServerConfig


# ---------------------------------------------------------------------------
# 配置文件路径
# ---------------------------------------------------------------------------

def _get_settings_json_path() -> Path:
    """获取 settings.json 路径（项目级 .claude/settings.json）。"""
    from startup.utils.config import get_project_settings_path

    return get_project_settings_path()


def _get_global_settings_json_path() -> Path:
    """获取全局 settings.json 路径（~/.claude/settings.json）。"""
    from startup.utils.config import get_config_home_dir, PROJECT_SETTINGS_FILENAME

    return get_config_home_dir() / PROJECT_SETTINGS_FILENAME


# ---------------------------------------------------------------------------
# 配置验证
# ---------------------------------------------------------------------------

def validate_mcp_config(name: str, config: dict) -> MCPServerConfig | None:
    """验证 MCP 配置格式，返回 MCPServerConfig 或 None。

    支持两种 transport 类型：
    - stdio：需要 command 字段，可选 args / env
    - sse：需要 url 字段
    """
    if not isinstance(config, dict):
        return None

    # 判断 transport 类型
    transport = config.get("type", "stdio")

    if transport == "sse":
        url = config.get("url")
        if not url or not isinstance(url, str):
            return None
        return MCPServerConfig(
            command="",
            args=[],
            env=config.get("env") if isinstance(config.get("env"), dict) else None,
            transport="sse",
            url=url,
        )

    # 默认 stdio
    command = config.get("command")
    if not command or not isinstance(command, str):
        return None

    args = config.get("args", [])
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        args = []

    env = config.get("env")
    if env is not None and not isinstance(env, dict):
        env = None
    if isinstance(env, dict):
        env = {k: v for k, v in env.items() if isinstance(k, str) and isinstance(v, str)}

    return MCPServerConfig(
        command=command,
        args=args,
        env=env,
        transport="stdio",
        url=config.get("url") if isinstance(config.get("url"), str) else None,
    )


# ---------------------------------------------------------------------------
# 配置读取
# ---------------------------------------------------------------------------

def _read_mcp_servers_from_json(path: Path) -> dict[str, MCPServerConfig]:
    """从 JSON 文件中读取 mcpServers 字段。"""
    if not path.exists():
        return {}

    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (json.JSONDecodeError, OSError):
        return {}

    if not isinstance(data, dict):
        return {}

    mcp_servers = data.get("mcpServers", {})
    if not isinstance(mcp_servers, dict):
        return {}

    result: dict[str, MCPServerConfig] = {}
    for name, config in mcp_servers.items():
        if not isinstance(name, str):
            continue
        validated = validate_mcp_config(name, config)
        if validated is not None:
            result[name] = validated

    return result


def get_mcp_server_configs() -> dict[str, MCPServerConfig]:
    """从配置文件读取 MCP 服务器配置。

    读取顺序（后覆盖前）：
    1. 全局 settings.json (~/.claude/settings.json)
    2. 项目 settings.json (.claude/settings.json)

    返回合并后的 MCPServerConfig 字典。
    """
    # 全局配置
    global_configs = _read_mcp_servers_from_json(_get_global_settings_json_path())

    # 项目配置（覆盖全局）
    project_configs = _read_mcp_servers_from_json(_get_settings_json_path())

    # 合并：项目配置覆盖全局
    merged = {**global_configs, **project_configs}
    return merged
