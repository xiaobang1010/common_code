"""standard kind 插件加载 — 自动检测 skills/commands/hooks/MCP 子目录。

standard 插件不需要写 Python 代码，只需按目录约定放置文件：
    skills/<name>/SKILL.md      → 合并到技能系统
    commands/<name>.md           → 合并到斜杠命令
    hooks/hooks.json             → 合并到 hooks 配置
    .mcp.json                    → 合并到 MCP 客户端配置
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from startup.plugins.manifest import LoadedPlugin

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# load_standard_plugin — 加载 standard kind 插件
# ---------------------------------------------------------------------------


def load_standard_plugin(plugin: LoadedPlugin) -> dict[str, Any]:
    """加载 standard kind 插件，返回检测到的能力清单。

    Args:
        plugin: 已加载的插件

    Returns:
        {
            "skills": [Skill, ...],
            "hooks_config": dict | None,
            "mcp_config": dict | None,
            "commands": [str, ...],
        }
    """
    plugin_dir = Path(plugin.manifest.path)
    result: dict[str, Any] = {
        "skills": [],
        "hooks_config": None,
        "mcp_config": None,
        "commands": [],
    }

    # 1. 检测 skills/ 子目录
    skills_dir = plugin_dir / "skills"
    if skills_dir.is_dir():
        result["skills"] = _load_plugin_skills(skills_dir, plugin.manifest.name)
        if result["skills"]:
            plugin.skills_registered = [s.name for s in result["skills"]]

    # 2. 检测 hooks/hooks.json
    hooks_file = plugin_dir / "hooks" / "hooks.json"
    if hooks_file.is_file():
        result["hooks_config"] = _load_hooks_config(hooks_file)
        if result["hooks_config"]:
            plugin.hooks_registered = list(result["hooks_config"].keys())

    # 3. 检测 .mcp.json
    mcp_file = plugin_dir / ".mcp.json"
    if mcp_file.is_file():
        result["mcp_config"] = _load_mcp_config(mcp_file)
        if result["mcp_config"]:
            plugin.mcp_servers_registered = list(result["mcp_config"].get("mcpServers", {}).keys())

    # 4. 检测 commands/ 子目录
    commands_dir = plugin_dir / "commands"
    if commands_dir.is_dir():
        result["commands"] = _load_plugin_commands(commands_dir)
        if result["commands"]:
            plugin.commands_registered = [c["name"] for c in result["commands"]]

    logger.debug(
        "standard 插件 %s 加载完成: %d skills, %d hooks, %d mcp, %d commands",
        plugin.manifest.name,
        len(result["skills"]),
        len(result["hooks"]) if result["hooks_config"] else 0,
        len(result["mcp_servers"]) if result["mcp_config"] else 0,
        len(result["commands"]),
    )

    return result


# ---------------------------------------------------------------------------
# _load_plugin_skills — 加载插件 skills/ 子目录
# ---------------------------------------------------------------------------


def _load_plugin_skills(skills_dir: Path, plugin_name: str) -> list:
    """加载插件 skills/ 子目录下的所有技能。

    每个子目录是一个 skill，含 SKILL.md。
    复用 tools/skills/loader._load_skill_file 加载。
    """
    from tools.skills.loader import _load_skill_file

    skills = []
    for entry in sorted(skills_dir.iterdir()):
        if not entry.is_dir():
            continue

        skill_file = entry / "SKILL.md"
        if not skill_file.is_file():
            continue

        skill = _load_skill_file(skill_file, entry.name)
        if skill is not None:
            # 标记来源为插件
            skill.source = "plugin"
            skills.append(skill)

    return skills


# ---------------------------------------------------------------------------
# _load_hooks_config — 加载插件 hooks.json
# ---------------------------------------------------------------------------


def _load_hooks_config(hooks_file: Path) -> dict | None:
    """加载插件的 hooks/hooks.json 配置。"""
    import json

    try:
        return json.loads(hooks_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("hooks.json 解析失败 %s: %s", hooks_file, e)
        return None


# ---------------------------------------------------------------------------
# _load_mcp_config — 加载插件 .mcp.json
# ---------------------------------------------------------------------------


def _load_mcp_config(mcp_file: Path) -> dict | None:
    """加载插件的 .mcp.json 配置。"""
    import json

    try:
        return json.loads(mcp_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(".mcp.json 解析失败 %s: %s", mcp_file, e)
        return None


# ---------------------------------------------------------------------------
# _load_plugin_commands — 加载插件 commands/ 子目录
# ---------------------------------------------------------------------------


def _load_plugin_commands(commands_dir: Path) -> list[dict]:
    """加载插件 commands/ 子目录下的 slash 命令。

    每个 .md 文件是一个命令，文件名是命令名，正文是命令 prompt。
    """
    commands = []
    for entry in sorted(commands_dir.iterdir()):
        if not entry.is_file() or entry.suffix != ".md":
            continue

        cmd_name = entry.stem
        try:
            content = entry.read_text(encoding="utf-8")
            commands.append({"name": cmd_name, "content": content})
        except OSError as e:
            logger.warning("命令文件读取失败 %s: %s", entry, e)

    return commands


# ---------------------------------------------------------------------------
# get_all_plugin_skills — 获取所有启用 standard 插件提供的技能
# ---------------------------------------------------------------------------


def get_all_plugin_skills() -> list:
    """获取所有启用的 standard 插件提供的技能列表。"""
    from startup.plugins.manager import PluginManager

    all_skills = []
    for plugin in PluginManager.get_enabled_by_kind("standard"):
        result = load_standard_plugin(plugin)
        all_skills.extend(result["skills"])

    return all_skills


# ---------------------------------------------------------------------------
# get_all_plugin_hooks — 获取所有启用 standard 插件提供的 hooks
# ---------------------------------------------------------------------------


def get_all_plugin_hooks() -> dict:
    """获取所有启用的 standard 插件提供的 hooks 配置，合并为一个 dict。"""
    from startup.plugins.manager import PluginManager

    merged: dict[str, Any] = {}
    for plugin in PluginManager.get_enabled_by_kind("standard"):
        result = load_standard_plugin(plugin)
        if result["hooks_config"]:
            for key, value in result["hooks_config"].items():
                if key in merged:
                    # 合并列表型配置
                    if isinstance(merged[key], list) and isinstance(value, list):
                        merged[key].extend(value)
                    else:
                        merged[key] = value
                else:
                    merged[key] = value

    return merged


# ---------------------------------------------------------------------------
# get_all_plugin_mcp — 获取所有启用 standard 插件提供的 MCP 配置
# ---------------------------------------------------------------------------


def get_all_plugin_mcp() -> dict:
    """获取所有启用的 standard 插件提供的 MCP 服务器配置，合并为一个 dict。"""
    from startup.plugins.manager import PluginManager

    merged: dict[str, Any] = {"mcpServers": {}}
    for plugin in PluginManager.get_enabled_by_kind("standard"):
        result = load_standard_plugin(plugin)
        if result["mcp_config"]:
            servers = result["mcp_config"].get("mcpServers", {})
            merged["mcpServers"].update(servers)

    return merged
