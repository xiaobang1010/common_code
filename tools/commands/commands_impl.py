"""核心斜杠命令实现。

参考原始 TypeScript 实现 src/commands/ 下各命令目录。

每个命令处理函数签名：
    async def cmd_xxx(context: CommandContext) -> str

返回值为命令输出文本。
"""

from __future__ import annotations

import random

from tools.commands.commands_context import CommandContext
from tools.commands.commands import Command, get_commands, find_command


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
        from startup.state.app_state import TokenUsage

        state = context.app_state.get_state()
        state.total_cost_usd = 0.0
        state.token_usage = TokenUsage()

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

        if context.app_state is not None:
            state = context.app_state.get_state()
            lines.append(f"  model: {state.model or 'default'}")

        if context.config is not None:
            lines.append(f"  max_tokens: {context.config.max_tokens}")
            lines.append(f"  temperature: {context.config.temperature}")

        try:
            from startup.bootstrap.state import get_permission_mode
            lines.append(f"  permission_mode: {get_permission_mode()}")
        except ImportError:
            pass

        try:
            from startup.config import get_global_config
            gcfg = get_global_config()
            lines.append(f"  llm_base_url: {gcfg.llm_base_url or 'default'}")
            lines.append(f"  llm_model: {gcfg.llm_model or 'default'}")
        except Exception:
            lines.append("  (global config not available)")

        lines.append("")
        lines.append("Usage: /config model <name>  - Change model")

        return "\n".join(lines)

    # 有参数：修改配置
    parts = args.split(None, 1)
    key = parts[0].lower()
    value = parts[1] if len(parts) > 1 else ""

    if key == "model" and value:
        if context.app_state is not None:
            context.app_state.get_state().model = value
        try:
            from startup.bootstrap.state import set_model
            set_model(value)
        except ImportError:
            pass
        return f"Model changed to: {value}"

    return f"Unknown config key: {key}. Type /config to see available options."


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
        context.app_state.get_state().model = new_model
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
    """显示当前会话的成本统计。

    统一从 AppState 读取累计成本和 token 用量，不再读 bootstrap state 的独立副本。
    """
    lines: list[str] = ["Session cost:", ""]

    # 统一从 app_state 读取
    if context.app_state is not None:
        state = context.app_state.get_state()
        lines.append(f"  Total cost: ${state.total_cost_usd:.4f}")
        usage = state.token_usage
        lines.append(f"  Input tokens: {usage.input_tokens}")
        lines.append(f"  Output tokens: {usage.output_tokens}")
        lines.append(f"  Cache read tokens: {usage.cache_read_input_tokens}")
        lines.append(f"  Cache creation tokens: {usage.cache_creation_input_tokens}")
    else:
        lines.append("  (state unavailable)")

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
