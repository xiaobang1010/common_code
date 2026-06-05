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
# 测试块
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 60)
    print("命令注册表测试")
    print("=" * 60)

    # 重置注册表
    clear_commands()

    # ---- 测试 1: get_commands 返回内置命令 ----
    print("\n--- 测试 1: get_commands 返回内置命令 ---")
    commands = get_commands()
    assert "help" in commands
    assert "clear" in commands
    assert "compact" in commands
    assert "config" in commands
    assert "model" in commands
    assert "cost" in commands
    assert "exit" in commands
    print(f"  内置命令数: {len(commands)}")
    for name, cmd in sorted(commands.items()):
        aliases_str = f" (aliases: {', '.join(cmd.aliases)})" if cmd.aliases else ""
        print(f"  /{name}{aliases_str} — {cmd.description}")
    print("  [PASS] get_commands 返回内置命令")

    # ---- 测试 2: find_command 按名称查找 ----
    print("\n--- 测试 2: find_command 按名称查找 ---")
    cmd = find_command("help")
    assert cmd is not None
    assert cmd.name == "help"
    assert "Show help" in cmd.description
    print(f"  find_command('help') = {cmd.name}: {cmd.description}")

    cmd_clear = find_command("clear")
    assert cmd_clear is not None
    assert cmd_clear.name == "clear"
    print(f"  find_command('clear') = {cmd_clear.name}")
    print("  [PASS] find_command 按名称查找")

    # ---- 测试 3: find_command 按别名查找 ----
    print("\n--- 测试 3: find_command 按别名查找 ---")
    cmd_h = find_command("h")
    assert cmd_h is not None
    assert cmd_h.name == "help"
    print(f"  find_command('h') = {cmd_h.name}")

    cmd_q = find_command("q")
    assert cmd_q is not None
    assert cmd_q.name == "exit"
    print(f"  find_command('q') = {cmd_q.name}")

    cmd_reset = find_command("reset")
    assert cmd_reset is not None
    assert cmd_reset.name == "clear"
    print(f"  find_command('reset') = {cmd_reset.name}")

    cmd_settings = find_command("settings")
    assert cmd_settings is not None
    assert cmd_settings.name == "config"
    print(f"  find_command('settings') = {cmd_settings.name}")
    print("  [PASS] find_command 按别名查找")

    # ---- 测试 4: find_command 不存在的命令 ----
    print("\n--- 测试 4: find_command 不存在的命令 ---")
    cmd_none = find_command("nonexistent")
    assert cmd_none is None
    print(f"  find_command('nonexistent') = {cmd_none}")
    print("  [PASS] find_command 不存在的命令返回 None")

    # ---- 测试 5: register_command 注册新命令 ----
    print("\n--- 测试 5: register_command 注册新命令 ---")

    async def cmd_test(ctx: CommandContext) -> str:
        return "test output"

    new_cmd = Command(name="test", description="Test command", handler=cmd_test, aliases=["t"])
    register_command(new_cmd)
    assert find_command("test") is not None
    assert find_command("t") is not None
    assert find_command("t").name == "test"
    print(f"  注册 /test (alias: t) 成功")
    print("  [PASS] register_command 注册新命令")

    # ---- 测试 6: register_command 名称冲突 ----
    print("\n--- 测试 6: register_command 名称冲突 ---")
    dup_cmd = Command(name="help", description="Duplicate", handler=cmd_test)
    try:
        register_command(dup_cmd)
        assert False, "应抛出 ValueError"
    except ValueError as e:
        print(f"  冲突检测: {e}")
        print("  [PASS] register_command 名称冲突检测")

    # ---- 测试 7: clear_commands 重置 ----
    print("\n--- 测试 7: clear_commands 重置 ---")
    clear_commands()
    commands_after = get_commands()
    assert "help" in commands_after  # 重新初始化
    print(f"  clear_commands 后重新初始化: {len(commands_after)} 个命令")
    print("  [PASS] clear_commands 重置")

    print("\n" + "=" * 60)
    print("所有测试完成")
    print("=" * 60)
