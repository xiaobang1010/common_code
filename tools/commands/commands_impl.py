"""核心斜杠命令实现。

参考原始 TypeScript 实现 src/commands/ 下各命令目录。

每个命令处理函数签名：
    async def cmd_xxx(context: CommandContext) -> str

返回值为命令输出文本。
"""

from __future__ import annotations

import random
from typing import Any

from tools.commands.commands_context import CommandContext
from tools.commands.commands import Command, get_commands, find_command
from tools.spec import create_spec, update_spec, spec_exists, list_specs


# ---------------------------------------------------------------------------
# /help — 显示命令列表和帮助信息
# ---------------------------------------------------------------------------


async def cmd_help(context: CommandContext) -> str:
    """显示所有可用命令的帮助信息。"""
    commands = get_commands()
    lines: list[str] = ["Available commands:", ""]

    # 按名称排序
    for name in sorted(commands.keys()):
        cmd = commands[name]
        aliases_str = ""
        if cmd.aliases:
            aliases_str = f" ({', '.join(cmd.aliases)})"
        lines.append(f"  /{name}{aliases_str} — {cmd.description}")

    lines.append("")
    lines.append("Type /<command> to run a command. Use /exit to quit.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# /clear — 清除对话历史
# ---------------------------------------------------------------------------


async def cmd_clear(context: CommandContext) -> str:
    """清除对话历史。"""
    # 清空消息列表
    context.messages.clear()

    # 如果有 REPL 引用，也清空其消息
    if context.repl is not None and hasattr(context.repl, "_messages"):
        context.repl._messages.clear()

    # 重置成本状态
    if context.app_state is not None:
        try:
            from startup.bootstrap.state import reset_cost_state
            reset_cost_state()
        except ImportError:
            pass

    return "Conversation cleared."


# ---------------------------------------------------------------------------
# /compact — 手动触发压缩
# ---------------------------------------------------------------------------


async def cmd_compact(context: CommandContext) -> str:
    """手动触发对话压缩。"""
    if not context.messages:
        return "No messages to compact."

    # 如果有压缩函数，使用它
    if context.compact_fn is not None:
        try:
            model = "gpt-4o"
            if context.config is not None:
                model = context.config.model
            elif context.app_state is not None:
                state = context.app_state.get_state()
                if state.model:
                    model = state.model

            compacted = await context.compact_fn(
                messages=context.messages,
                model=model,
            )
            if compacted is not None:
                context.messages.clear()
                context.messages.extend(compacted)
                return "Conversation compacted."
            else:
                return "Compaction returned no result."
        except Exception as e:
            return f"Compaction error: {e}"

    # 如果有 REPL 引用，使用简单的折叠
    if context.repl is not None and hasattr(context.repl, "_messages"):
        try:
            from components.messages import fold_messages
            context.repl._messages = fold_messages(context.repl._messages, max_visible=20)
            return "Conversation compacted (folded)."
        except ImportError:
            pass

    # 降级：仅保留最近的消息
    if len(context.messages) > 10:
        kept = context.messages[-10:]
        context.messages.clear()
        context.messages.extend(kept)
        return "Conversation compacted (kept last 10 messages)."

    return "Not enough messages to compact."


# ---------------------------------------------------------------------------
# /config — 查看/修改配置
# ---------------------------------------------------------------------------


async def cmd_config(context: CommandContext) -> str:
    """查看或修改配置。"""
    args = context.args.strip()

    # 无参数：显示当前配置概要
    if not args:
        lines: list[str] = ["Current configuration:", ""]

        # 从 app_state 读取
        if context.app_state is not None:
            state = context.app_state.get_state()
            lines.append(f"  model: {state.model or 'default'}")
            lines.append(f"  verbose: {state.verbose}")
            lines.append(f"  theme: {state.theme}")
            lines.append(f"  permission_mode: {state.permission_mode}")
            lines.append(f"  auto_compact: {state.auto_compact_config.enabled}")
            lines.append(f"  context_collapse: {state.context_collapse_enabled}")

        # 从 config 读取
        if context.config is not None:
            lines.append(f"  max_tokens: {context.config.max_tokens}")
            lines.append(f"  temperature: {context.config.temperature}")

        # 从全局配置文件读取
        try:
            from startup.utils.config import get_global_config
            gcfg = get_global_config()
            lines.append(f"  llm_base_url: {gcfg.llm_base_url or 'default'}")
            lines.append(f"  llm_model: {gcfg.llm_model or 'default'}")
        except Exception:
            lines.append("  (global config not available)")

        lines.append("")
        lines.append("Usage: /config <key> <value>")
        lines.append("  /config model <name>  — Change model")
        lines.append("  /config verbose <on|off>  — Toggle verbose mode")
        lines.append("  /config theme <dark|light>  — Change theme")

        return "\n".join(lines)

    # 有参数：修改配置
    parts = args.split(None, 1)
    key = parts[0].lower()
    value = parts[1] if len(parts) > 1 else ""

    if key == "model" and value:
        if context.app_state is not None:
            context.app_state.set_state(lambda s: _set_field(s, "model", value))
        try:
            from startup.bootstrap.state import set_model
            set_model(value)
        except ImportError:
            pass
        return f"Model changed to: {value}"

    elif key == "verbose":
        verbose_val = value.lower() in ("on", "true", "1", "yes")
        if context.app_state is not None:
            context.app_state.set_state(lambda s: _set_field(s, "verbose", verbose_val))
        try:
            from startup.bootstrap.state import set_verbose
            set_verbose(verbose_val)
        except ImportError:
            pass
        return f"Verbose mode: {'on' if verbose_val else 'off'}"

    elif key == "theme" and value.lower() in ("dark", "light"):
        if context.app_state is not None:
            context.app_state.set_state(lambda s: _set_field(s, "theme", value.lower()))
        return f"Theme changed to: {value.lower()}"

    else:
        return f"Unknown config key: {key}. Type /config to see available options."


def _set_field(state: Any, field_name: str, value: Any) -> Any:
    """AppState 更新辅助函数。"""
    setattr(state, field_name, value)
    return state


# ---------------------------------------------------------------------------
# /model — 查看/切换模型
# ---------------------------------------------------------------------------


async def cmd_model(context: CommandContext) -> str:
    """查看或切换当前模型。"""
    args = context.args.strip()

    # 无参数：显示当前模型
    if not args:
        model = None
        if context.app_state is not None:
            state = context.app_state.get_state()
            model = state.model
        if context.config is not None:
            model = model or context.config.model
        if model is None:
            try:
                from startup.bootstrap.state import get_model
                model = get_model()
            except ImportError:
                pass
        return f"Current model: {model or 'default'}"

    # 有参数：切换模型
    new_model = args.strip()
    if context.app_state is not None:
        context.app_state.set_state(lambda s: _set_field(s, "model", new_model))
    try:
        from startup.bootstrap.state import set_model
        set_model(new_model)
    except ImportError:
        pass
    return f"Model changed to: {new_model}"


# ---------------------------------------------------------------------------
# /cost — 显示成本统计
# ---------------------------------------------------------------------------


async def cmd_cost(context: CommandContext) -> str:
    """显示当前会话的成本统计。"""
    lines: list[str] = ["Session cost:", ""]

    # 从 app_state 读取
    if context.app_state is not None:
        state = context.app_state.get_state()
        lines.append(f"  Total cost: ${state.total_cost_usd:.4f}")
        usage = state.token_usage
        lines.append(f"  Input tokens: {usage.input_tokens}")
        lines.append(f"  Output tokens: {usage.output_tokens}")
        lines.append(f"  Cache read tokens: {usage.cache_read_input_tokens}")
        lines.append(f"  Cache creation tokens: {usage.cache_creation_input_tokens}")

    # 从 bootstrap state 读取
    try:
        from startup.bootstrap.state import (
            get_total_cost_usd,
            get_total_input_tokens,
            get_total_output_tokens,
            get_total_duration,
        )
        cost = get_total_cost_usd()
        input_tokens = get_total_input_tokens()
        output_tokens = get_total_output_tokens()
        duration_ms = get_total_duration()

        lines.append("")
        lines.append(f"  Accumulated cost: ${cost:.4f}")
        lines.append(f"  Accumulated input tokens: {input_tokens}")
        lines.append(f"  Accumulated output tokens: {output_tokens}")
        lines.append(f"  Session duration: {duration_ms / 1000:.1f}s")
    except ImportError:
        pass

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# /exit — 退出 REPL
# ---------------------------------------------------------------------------

_GOODBYE_MESSAGES = ["Goodbye!", "See ya!", "Bye!", "Catch you later!"]


async def cmd_exit(context: CommandContext) -> str:
    """退出 REPL。"""
    # 如果有 REPL 引用，标记退出
    if context.repl is not None and hasattr(context.repl, "stop"):
        context.repl.stop()

    return random.choice(_GOODBYE_MESSAGES)


# ---------------------------------------------------------------------------
# /spec — Spec 驱动开发
# ---------------------------------------------------------------------------


async def cmd_spec(context: CommandContext) -> str:
    """根据描述生成 spec 文档（spec.md / tasks.md / checklist.md）。"""
    args = context.args.strip()

    # 无参数：显示帮助
    if not args:
        return (
            "Spec mode — spec-driven development\n\n"
            "Usage: /spec <description>\n\n"
            "Generates spec.md / tasks.md / checklist.md under .agent/specs/<change-id>/\n"
            "based on your description and codebase context.\n\n"
            "If a matching spec already exists, it will be updated."
        )

    # 从描述生成 change-id：取前几个词，用连字符连接，转小写
    import re
    words = re.sub(r"[^\w\s-]", "", args.lower()).split()
    change_id = "-".join(words[:4]) if words else "unnamed-spec"

    # 获取 project_root
    project_root = context.project_root
    if not project_root:
        try:
            from startup.bootstrap.state import get_cwd
            project_root = get_cwd()
        except ImportError:
            project_root = "."

    # 检查现有 spec
    existing_specs = list_specs(project_root)

    # 查找语义匹配的现有 spec（change-id 包含关系或相同）
    matched_change_id = None
    for spec_info in existing_specs:
        if change_id == spec_info["change_id"] or change_id in spec_info["change_id"] or spec_info["change_id"] in change_id:
            matched_change_id = spec_info["change_id"]
            break

    if matched_change_id and spec_exists(project_root, matched_change_id):
        # 更新现有 spec
        result = update_spec(project_root, matched_change_id, args)
        return f"Updated existing spec '{matched_change_id}':\n{result}\n\nReview the changes and approve before implementation."
    else:
        # 创建新 spec
        path = create_spec(project_root, change_id, args)
        return (
            f"Created new spec '{change_id}':\n"
            f"  {path}/spec.md\n"
            f"  {path}/tasks.md\n"
            f"  {path}/checklist.md\n\n"
            f"Review the spec and approve before implementation."
        )


# ---------------------------------------------------------------------------
# 测试块
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import asyncio

    print("=" * 60)
    print("核心命令实现测试")
    print("=" * 60)

    # ---- 测试 1: 所有命令的创建和注册 ----
    print("\n--- 测试 1: 所有命令的创建和注册 ---")
    from tools.commands.commands import get_commands, clear_commands

    clear_commands()
    commands = get_commands()
    expected_names = {"help", "clear", "compact", "config", "model", "cost", "exit"}
    assert expected_names == set(commands.keys()), f"期望 {expected_names}, 得到 {set(commands.keys())}"
    print(f"  注册命令: {sorted(commands.keys())}")
    for name, cmd in sorted(commands.items()):
        assert cmd.handler is not None, f"{name} 缺少 handler"
        assert cmd.description, f"{name} 缺少 description"
        print(f"  /{name}: {cmd.description}")
    print("  [PASS] 所有命令的创建和注册")

    # ---- 测试 2: find_command 查找 ----
    print("\n--- 测试 2: find_command 查找 ---")
    assert find_command("help") is not None
    assert find_command("h") is not None
    assert find_command("?") is not None
    assert find_command("h").name == "help"
    assert find_command("?").name == "help"

    assert find_command("exit") is not None
    assert find_command("quit") is not None
    assert find_command("q") is not None
    assert find_command("quit").name == "exit"
    assert find_command("q").name == "exit"

    assert find_command("clear") is not None
    assert find_command("reset") is not None
    assert find_command("new") is not None

    assert find_command("config") is not None
    assert find_command("settings") is not None

    assert find_command("nonexistent") is None
    print("  [PASS] find_command 查找")

    # ---- 测试 3: cmd_help ----
    print("\n--- 测试 3: cmd_help ---")

    async def _test_help():
        ctx = CommandContext()
        result = await cmd_help(ctx)
        assert "Available commands" in result
        assert "/help" in result
        assert "/exit" in result
        print(f"  输出:\n{result}")
        print("  [PASS] cmd_help")

    asyncio.run(_test_help())

    # ---- 测试 4: cmd_clear ----
    print("\n--- 测试 4: cmd_clear ---")

    async def _test_clear():
        ctx = CommandContext(messages=[{"role": "user", "content": "hello"}])
        result = await cmd_clear(ctx)
        assert result == "Conversation cleared."
        assert len(ctx.messages) == 0
        print(f"  输出: {result}")
        print(f"  消息数: {len(ctx.messages)}")
        print("  [PASS] cmd_clear")

    asyncio.run(_test_clear())

    # ---- 测试 5: cmd_model ----
    print("\n--- 测试 5: cmd_model ---")

    async def _test_model():
        # 无参数
        ctx = CommandContext()
        result = await cmd_model(ctx)
        assert "Current model" in result
        print(f"  无参数: {result}")

        # 有参数
        ctx2 = CommandContext(args="gpt-4o-mini")
        result2 = await cmd_model(ctx2)
        assert "gpt-4o-mini" in result2
        print(f"  切换模型: {result2}")
        print("  [PASS] cmd_model")

    asyncio.run(_test_model())

    # ---- 测试 6: cmd_cost ----
    print("\n--- 测试 6: cmd_cost ---")

    async def _test_cost():
        ctx = CommandContext()
        result = await cmd_cost(ctx)
        assert "Session cost" in result
        print(f"  输出:\n{result}")
        print("  [PASS] cmd_cost")

    asyncio.run(_test_cost())

    # ---- 测试 7: cmd_exit ----
    print("\n--- 测试 7: cmd_exit ---")

    async def _test_exit():
        ctx = CommandContext()
        result = await cmd_exit(ctx)
        assert result in _GOODBYE_MESSAGES
        print(f"  输出: {result}")
        print("  [PASS] cmd_exit")

    asyncio.run(_test_exit())

    # ---- 测试 8: cmd_compact ----
    print("\n--- 测试 8: cmd_compact ---")

    async def _test_compact():
        # 空消息
        ctx = CommandContext(messages=[])
        result = await cmd_compact(ctx)
        assert "No messages" in result
        print(f"  空消息: {result}")

        # 降级模式
        msgs = [{"role": "user", "content": f"Message {i}"} for i in range(20)]
        ctx2 = CommandContext(messages=msgs)
        result2 = await cmd_compact(ctx2)
        assert "compacted" in result2.lower() or "compact" in result2.lower()
        print(f"  降级模式: {result2}")
        print("  [PASS] cmd_compact")

    asyncio.run(_test_compact())

    # ---- 测试 9: cmd_config ----
    print("\n--- 测试 9: cmd_config ---")

    async def _test_config():
        # 无参数
        ctx = CommandContext()
        result = await cmd_config(ctx)
        assert "Current configuration" in result or "config" in result.lower()
        print(f"  无参数输出长度: {len(result)}")

        # 未知 key
        ctx2 = CommandContext(args="unknown_key")
        result2 = await cmd_config(ctx2)
        assert "Unknown" in result2 or "unknown" in result2.lower()
        print(f"  未知 key: {result2}")

        print("  [PASS] cmd_config")

    asyncio.run(_test_config())

    print("\n" + "=" * 60)
    print("所有测试完成")
    print("=" * 60)
