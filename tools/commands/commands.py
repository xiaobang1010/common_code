"""斜杠命令注册表 — 命令定义、注册、查找。

参考原始 TypeScript 实现 src/commands.ts 中的 getCommands / findCommand。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Coroutine

from tools.commands.commands_context import CommandContext


# ---------------------------------------------------------------------------
# Command dataclass
# ---------------------------------------------------------------------------


@dataclass
class Command:
    """斜杠命令定义。

    Attributes:
        name: 命令名（不含 /），如 "help"
        description: 命令描述
        handler: 异步处理函数 async (CommandContext) -> str
        aliases: 命令别名列表，如 ["h", "?"]
    """

    name: str
    description: str
    handler: Callable[[CommandContext], Coroutine[Any, Any, str]]
    aliases: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 全局命令注册表
# ---------------------------------------------------------------------------

# 命令注册表：name -> Command
_registry: dict[str, Command] = {}

# 是否已初始化内置命令
_initialized: bool = False


# ---------------------------------------------------------------------------
# 初始化内置命令
# ---------------------------------------------------------------------------


def _init_builtin_commands() -> None:
    """延迟注册内置命令，避免循环 import。"""
    global _initialized
    if _initialized:
        return
    _initialized = True

    from tools.commands.commands_impl import (
        cmd_help,
        cmd_clear,
        cmd_compact,
        cmd_config,
        cmd_model,
        cmd_cost,
        cmd_exit,
    )

    builtin_commands = [
        Command(name="help", description="Show help and available commands", handler=cmd_help, aliases=["h", "?"]),
        Command(name="clear", description="Clear conversation history and free up context", handler=cmd_clear, aliases=["reset", "new"]),
        Command(name="compact", description="Compact conversation context. Optional: /compact [instructions]", handler=cmd_compact),
        Command(name="config", description="View or modify configuration", handler=cmd_config, aliases=["settings"]),
        Command(name="model", description="View or switch the current model", handler=cmd_model),
        Command(name="cost", description="Show the total cost and duration of the current session", handler=cmd_cost),
        Command(name="exit", description="Exit the REPL", handler=cmd_exit, aliases=["quit", "q"]),
    ]

    for cmd in builtin_commands:
        _registry[cmd.name] = cmd


# ---------------------------------------------------------------------------
# 公开 API
# ---------------------------------------------------------------------------


def get_commands() -> dict[str, Command]:
    """返回命令注册表（延迟初始化内置命令）。

    Returns:
        命令名字到 Command 的映射
    """
    _init_builtin_commands()
    return dict(_registry)


def register_command(command: Command) -> None:
    """注册一个命令到注册表。

    Args:
        command: Command 实例

    Raises:
        ValueError: 如果命令名或别名已被占用
    """
    _init_builtin_commands()

    # 检查名称冲突
    if command.name in _registry:
        raise ValueError(f"Command name '{command.name}' is already registered")

    # 检查别名冲突
    for alias in command.aliases:
        existing = find_command(alias)
        if existing is not None:
            raise ValueError(
                f"Alias '{alias}' conflicts with existing command '{existing.name}'"
            )

    _registry[command.name] = command


def find_command(name: str) -> Command | None:
    """按名称或别名查找命令。

    Args:
        name: 命令名或别名（不含 /）

    Returns:
        Command 实例或 None
    """
    _init_builtin_commands()

    # 先按名称查找
    if name in _registry:
        return _registry[name]

    # 再按别名查找
    for cmd in _registry.values():
        if name in cmd.aliases:
            return cmd

    return None


def clear_commands() -> None:
    """清空注册表（仅用于测试）。"""
    global _initialized
    _registry.clear()
    _initialized = False


# ---------------------------------------------------------------------------
# try_resolve_skill — 尝试将 /name 解析为 skill 触发
# ---------------------------------------------------------------------------


def try_resolve_skill(name: str, args: str) -> dict | None:
    """尝试将斜杠命令 /name 解析为 skill 触发。

    在 find_command 返回 None 时调用。如果 name 匹配一个
    user_invocable 的 skill，返回 skill 正文作为 user 消息（system-reminder 包裹）。
    否则返回 None。

    Args:
        name: 命令名（不含 /）
        args: 命令参数字符串

    Returns:
        skill 正文消息 dict，或 None（不是 skill）
    """
    from tools.skills.bundled import find_skill_by_name

    skill = find_skill_by_name(name)
    if skill is None or not skill.is_user_invocable():
        return None

    try:
        prompt = skill.resolve_prompt(args)
    except Exception:
        return None

    if not prompt.strip():
        return None

    return {
        "role": "user",
        "content": (
            "<system-reminder>\n"
            f"{prompt}\n"
            "</system-reminder>\n"
        ),
    }
