"""REPL 主屏幕。

参考原始 TypeScript 实现: src/screens/REPL.tsx

提供 REPL 主循环、消息渲染、输入处理和状态栏显示。
集成斜杠命令系统，通过 commands.find_command 路由到对应 handler。
"""

from __future__ import annotations

import asyncio
import enum
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from startup.state.app_state import AppState, AppStateProvider

from ink.ui.messages import MessageData, fold_messages, normalize_messages
from ink.ui.message import render_message
from ink.ui.prompt_input import PromptInput, InputMode

from tools.commands.commands import find_command, get_commands
from tools.commands.commands_context import CommandContext


# ---------------------------------------------------------------------------
# Screen 类型
# ---------------------------------------------------------------------------

class ScreenMode(enum.Enum):
    """屏幕模式。"""
    PROMPT = "prompt"
    TRANSCRIPT = "transcript"


# ---------------------------------------------------------------------------
# 斜杠命令处理结果
# ---------------------------------------------------------------------------

@dataclass
class CommandResult:
    """斜杠命令处理结果。"""
    handled: bool = True
    output: str = ""
    should_exit: bool = False


# ---------------------------------------------------------------------------
# REPLScreen — REPL 主屏幕
# ---------------------------------------------------------------------------

class REPLScreen:
    """REPL 主屏幕。

    管理消息列表、用户输入和渲染循环。

    Attributes:
        app_state: 应用状态提供者
        screen: 当前屏幕模式
        messages: 消息列表
        prompt_input: 输入组件
    """

    def __init__(self, app_state: AppStateProvider) -> None:
        self._app_state = app_state
        self._screen: ScreenMode = ScreenMode.PROMPT
        self._messages: list[MessageData] = []
        self._prompt_input = PromptInput(multiline=False)
        self._is_running = False
        self._on_submit: Optional[Callable[[str], Any]] = None

    @property
    def screen(self) -> ScreenMode:
        """当前屏幕模式。"""
        return self._screen

    @property
    def messages(self) -> list[MessageData]:
        """消息列表。"""
        return self._messages

    def set_on_submit(self, callback: Callable[[str], Any]) -> None:
        """设置用户提交回调。"""
        self._on_submit = callback

    # -----------------------------------------------------------------------
    # 主循环
    # -----------------------------------------------------------------------

    async def run(self) -> None:
        """REPL 主循环。

        不断读取用户输入，处理提交，渲染输出。
        """
        self._is_running = True
        state = self._app_state.get_state()

        # 渲染欢迎信息
        self._render_welcome(state)

        while self._is_running:
            try:
                # 渲染状态栏
                self.render_status_line()

                # 渲染提示符并获取输入
                user_input = await asyncio.to_thread(
                    self._prompt_input.get_input, "> "
                )

                if not user_input:
                    continue

                # 处理提交
                result = self.handle_prompt_submit(user_input)

                if result.should_exit:
                    self._is_running = False
                    break

                if result.output:
                    print(result.output)
                    continue

                # 普通消息 → 调用 LLM
                if not result.handled:
                    await self._call_llm()

            except (EOFError, KeyboardInterrupt):
                print("\nGoodbye!")
                self._is_running = False
                break

    def stop(self) -> None:
        """停止 REPL 循环。"""
        self._is_running = False

    # -----------------------------------------------------------------------
    # LLM 调用
    # -----------------------------------------------------------------------

    async def _call_llm(self) -> None:
        """调用 LLM 并流式输出响应，支持工具调用循环。"""
        from query.services.api.client import get_default_model
        from query.services.api.llm import query_model_with_streaming
        from tools import get_tools
        from tools.executor import execute_tool_call, ToolExecutionResult, tool_result_to_openai_message
        from tools.protocol import ToolUseContext

        model = self._app_state.get_state().model or get_default_model()
        tools = get_tools()
        tool_context = ToolUseContext()

        # Agentic 循环：LLM → tool_calls → 执行 → 反馈 → LLM → ...
        max_iterations = 20
        for iteration in range(max_iterations):
            openai_messages = self._messages_to_openai_format()
            if not openai_messages:
                return

            # 流式调用 LLM
            full_text = ""
            tool_calls_list: list[dict] = []
            tool_calls_map: dict[str, dict] = {}  # 按 id 聚合增量 tool_calls
            tool_calls_order: list[str] = []  # 保持插入顺序
            finish_reason = None

            try:
                async for event in query_model_with_streaming(
                    messages=openai_messages, tools=tools, model=model
                ):
                    if event.type == "content":
                        full_text += (event.content or "")
                        print(event.content or "", end="", flush=True)
                    elif event.type == "tool_call_delta":
                        tc_id = event.tool_call_id
                        if tc_id:
                            if tc_id not in tool_calls_map:
                                tool_calls_map[tc_id] = {
                                    "id": tc_id,
                                    "type": "function",
                                    "function": {
                                        "name": "",
                                        "arguments": "",
                                    },
                                }
                                tool_calls_order.append(tc_id)
                            entry = tool_calls_map[tc_id]
                            if event.tool_call_name:
                                entry["function"]["name"] += event.tool_call_name
                            if event.tool_call_arguments:
                                entry["function"]["arguments"] += event.tool_call_arguments
                    elif event.type == "done":
                        finish_reason = event.finish_reason
                    elif event.type == "usage" and event.usage:
                        state = self._app_state.get_state()
                        if event.usage.get("prompt_tokens"):
                            state.token_usage.input_tokens = event.usage["prompt_tokens"]
                        if event.usage.get("completion_tokens"):
                            state.token_usage.output_tokens = event.usage["completion_tokens"]
                    elif event.type == "error":
                        print(f"\nError: {event.content}")

            except Exception as e:
                print(f"\nError: {e}")
                break

            # 按插入顺序构建最终 tool_calls 列表
            tool_calls_list = [tool_calls_map[tc_id] for tc_id in tool_calls_order]

            # 将助手响应添加到消息列表
            if full_text or tool_calls_list:
                self._messages.append(MessageData(
                    role="assistant",
                    content=full_text or "",
                    tool_calls=tool_calls_list if tool_calls_list else None,
                ))

            # 如果没有工具调用，循环结束
            if not tool_calls_list or finish_reason != "tool_calls":
                if full_text:
                    print()  # 换行
                break

            print()  # 换行

            # 执行工具调用
            for tc in tool_calls_list:
                tool_name = tc.get("function", {}).get("name", "unknown")
                print(f"  \x1b[33m▸ {tool_name}\x1b[0m", flush=True)

                result: ToolExecutionResult = await execute_tool_call(
                    tc, tools, tool_context,
                )

                # 显示工具结果摘要
                result_preview = result.content[:200] + "..." if len(result.content) > 200 else result.content
                if result.is_error:
                    print(f"  \x1b[31m✗ {tool_name}: {result_preview}\x1b[0m")
                else:
                    print(f"  \x1b[32m✓ {tool_name}\x1b[0m", flush=True)

                # 将工具结果添加到消息列表
                tool_msg = tool_result_to_openai_message(result)
                self._messages.append(MessageData(
                    role="tool",
                    content=tool_msg.get("content", ""),
                    tool_call_id=tool_msg.get("tool_call_id", ""),
                ))

            # 继续循环，让 LLM 处理工具结果

    # -----------------------------------------------------------------------
    # 输入处理
    # -----------------------------------------------------------------------

    def handle_prompt_submit(self, input_text: str) -> CommandResult:
        """处理用户提交。

        - 斜杠命令检测和路由
        - 普通消息 → 添加到消息列表

        Args:
            input_text: 用户输入文本

        Returns:
            命令处理结果
        """
        # 斜杠命令检测
        if input_text.startswith("/"):
            return self._handle_slash_command(input_text)

        # 普通消息 → 添加到消息列表
        user_msg = MessageData(
            role="user",
            content=input_text,
        )
        self._messages.append(user_msg)

        # 添加到历史记录
        self._prompt_input.add_to_history(input_text)

        # 触发回调
        if self._on_submit:
            self._on_submit(input_text)

        return CommandResult(handled=False)

    def _handle_slash_command(self, command: str) -> CommandResult:
        """处理斜杠命令。

        通过 commands.find_command 查找命令并调用 handler。

        Args:
            command: 斜杠命令字符串

        Returns:
            命令处理结果
        """
        parts = command.strip().split(None, 1)
        cmd_name_with_slash = parts[0].lower()
        cmd_args = parts[1] if len(parts) > 1 else ""

        # 去掉前导 /
        cmd_name = cmd_name_with_slash.lstrip("/")

        # 通过命令注册表查找
        cmd = find_command(cmd_name)
        if cmd is None:
            return CommandResult(
                output=f"Unknown command: {cmd_name_with_slash}. Type /help for available commands."
            )

        # 构建 CommandContext
        # 将 MessageData 列表转为 OpenAI 格式 dict 列表
        openai_messages = self._messages_to_openai_format()
        ctx = CommandContext(
            messages=openai_messages,
            app_state=self._app_state,
            config=None,
            compact_fn=None,
            repl=self,
            args=cmd_args,
        )

        # 调用异步 handler
        try:
            output = asyncio.get_event_loop().run_until_complete(
                cmd.handler(ctx)
            )
        except RuntimeError:
            # 没有运行中的事件循环，创建新的
            output = asyncio.run(cmd.handler(ctx))

        # 同步 OpenAI 格式消息回 _messages
        self._sync_messages_from_openai_format(ctx.messages)

        # 判断是否为 exit 命令
        should_exit = cmd.name == "exit"

        return CommandResult(output=output, should_exit=should_exit)

    def _messages_to_openai_format(self) -> list[dict]:
        """将内部 MessageData 列表转为 OpenAI 格式 dict 列表。"""
        result: list[dict] = []
        for msg in self._messages:
            d: dict[str, Any] = {"role": msg.role}
            if msg.content:
                d["content"] = msg.content
            elif msg.tool_calls:
                # 有 tool_calls 但无 content 时，仍需包含 content 字段
                d["content"] = ""
            if msg.tool_calls:
                d["tool_calls"] = msg.tool_calls
            if msg.tool_call_id:
                d["tool_call_id"] = msg.tool_call_id
            result.append(d)
        return result

    def _sync_messages_from_openai_format(self, openai_messages: list[dict]) -> None:
        """将 OpenAI 格式消息同步回内部 MessageData 列表。

        仅在消息数量变化时更新（命令可能清空或压缩消息）。
        """
        if len(openai_messages) != len(self._messages):
            # 消息数量变化，重建列表
            self._messages.clear()
            for d in openai_messages:
                self._messages.append(MessageData(
                    role=d.get("role", "user"),
                    content=d.get("content", ""),
                    tool_calls=d.get("tool_calls"),
                    tool_call_id=d.get("tool_call_id"),
                ))

    # -----------------------------------------------------------------------
    # 渲染方法
    # -----------------------------------------------------------------------

    def render_messages(self) -> None:
        """渲染消息列表到终端。"""
        if not self._messages:
            return

        # 折叠消息
        visible = fold_messages(self._messages, max_visible=50)

        # 获取终端宽度
        width = self._get_terminal_width()

        # 渲染每条消息
        for msg in visible:
            rendered = render_message(msg, width=width)
            print(rendered)

    def render_prompt(self) -> None:
        """渲染输入提示符。"""
        state = self._app_state.get_state()
        if state.is_loading:
            print("\x1b[33m⏳\x1b[0m ", end="", flush=True)
        else:
            print("", end="", flush=True)

    def render_status_line(self) -> str:
        """渲染状态栏（模型名、token 使用、成本）。

        Returns:
            状态栏字符串
        """
        state = self._app_state.get_state()
        parts: list[str] = []

        # 模型名
        if state.model:
            parts.append(f"\x1b[36m{state.model}\x1b[0m")

        # Token 使用
        usage = state.token_usage
        if usage.input_tokens or usage.output_tokens:
            token_info = f"tokens: {usage.input_tokens}in/{usage.output_tokens}out"
            parts.append(f"\x1b[90m{token_info}\x1b[0m")

        # 成本
        if state.total_cost_usd > 0:
            cost_info = f"cost: ${state.total_cost_usd:.4f}"
            parts.append(f"\x1b[90m{cost_info}\x1b[0m")

        # 屏幕模式
        if self._screen == ScreenMode.TRANSCRIPT:
            parts.append("\x1b[90m[transcript]\x1b[0m")

        if parts:
            status = " │ ".join(parts)
            print(status)

        return " │ ".join(parts) if parts else ""

    def _render_welcome(self, state: AppState) -> None:
        """渲染欢迎信息。"""
        model = state.model or "default"
        print(f"\x1b[1mCommon Code\x1b[0m (model: {model})")
        print("Type /help for available commands.\n")

    # -----------------------------------------------------------------------
    # 辅助方法
    # -----------------------------------------------------------------------

    def add_message(self, msg: MessageData) -> None:
        """添加消息到列表。"""
        self._messages.append(msg)

    def add_assistant_response(self, content: str, tool_calls: list[dict] | None = None) -> None:
        """添加助手响应消息。"""
        msg = MessageData(
            role="assistant",
            content=content,
            tool_calls=tool_calls,
        )
        self._messages.append(msg)

    def add_tool_result(self, content: str, tool_call_id: str) -> None:
        """添加工具结果消息。"""
        msg = MessageData(
            role="tool",
            content=content,
            tool_call_id=tool_call_id,
        )
        self._messages.append(msg)

    def toggle_screen(self) -> None:
        """切换屏幕模式。"""
        if self._screen == ScreenMode.PROMPT:
            self._screen = ScreenMode.TRANSCRIPT
        else:
            self._screen = ScreenMode.PROMPT

    @staticmethod
    def _get_terminal_width() -> int:
        """获取终端宽度。"""
        try:
            import shutil
            size = shutil.get_terminal_size()
            return size.columns
        except (AttributeError, ValueError):
            return 80


# ---------------------------------------------------------------------------
# 测试块
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import asyncio as _asyncio

    print("=== repl.py 测试 ===\n")

    # 创建 AppStateProvider
    provider = AppStateProvider()
    state = provider.get_state()
    state.model = "claude-sonnet-4-20250514"
    state.total_cost_usd = 0.0523
    state.token_usage.input_tokens = 1500
    state.token_usage.output_tokens = 800

    # 创建 REPLScreen
    repl = REPLScreen(provider)

    # 1. 测试斜杠命令路由（通过命令注册表）
    print("--- 斜杠命令路由 ---")

    # /help
    result = repl.handle_prompt_submit("/help")
    print(f"  /help: handled={result.handled}, should_exit={result.should_exit}")
    assert "Available commands" in result.output
    print(f"  输出包含 'Available commands': OK")

    # /model
    result2 = repl.handle_prompt_submit("/model")
    print(f"  /model: {result2.output}")
    assert "model" in result2.output.lower()

    # /cost
    result3 = repl.handle_prompt_submit("/cost")
    print(f"  /cost:\n{result3.output}\n")
    assert "cost" in result3.output.lower() or "token" in result3.output.lower()

    # /clear
    repl.add_message(MessageData(role="user", content="test"))
    print(f"  添加消息后数量: {len(repl.messages)}")
    result4 = repl.handle_prompt_submit("/clear")
    print(f"  /clear: {result4.output}")
    assert len(repl.messages) == 0
    print(f"  清除后数量: {len(repl.messages)}")

    # /compact
    for i in range(60):
        repl.add_message(MessageData(role="user", content=f"Message {i}"))
    print(f"  添加60条消息后数量: {len(repl.messages)}")
    result5 = repl.handle_prompt_submit("/compact")
    print(f"  /compact: {result5.output}")

    # /exit
    result6 = repl.handle_prompt_submit("/exit")
    print(f"  /exit: should_exit={result6.should_exit}")
    assert result6.should_exit is True

    # 未知命令
    result7 = repl.handle_prompt_submit("/unknown")
    print(f"  /unknown: {result7.output}")
    assert "Unknown" in result7.output or "unknown" in result7.output.lower()

    # 别名测试
    result_h = repl.handle_prompt_submit("/h")
    assert "Available commands" in result_h.output
    print(f"  /h (alias for help): OK")

    result_q = repl.handle_prompt_submit("/q")
    assert result_q.should_exit is True
    print(f"  /q (alias for exit): OK")

    # 2. 测试普通消息提交
    print("\n--- 普通消息提交 ---")
    repl._messages.clear()
    result8 = repl.handle_prompt_submit("Hello, how are you?")
    print(f"  普通消息: handled={result8.handled}")
    print(f"  消息数量: {len(repl.messages)}")
    print(f"  最后消息: {repl.messages[-1].content}")

    # 3. 测试渲染状态栏
    print("\n--- 状态栏 ---")
    status = repl.render_status_line()
    print(f"  状态栏文本: {status}")

    # 4. 测试屏幕切换
    print("\n--- 屏幕切换 ---")
    print(f"  初始模式: {repl.screen.value}")
    repl.toggle_screen()
    print(f"  切换后模式: {repl.screen.value}")
    repl.toggle_screen()
    print(f"  再次切换: {repl.screen.value}")

    # 5. 测试辅助方法
    print("\n--- 辅助方法 ---")
    repl.add_assistant_response("I'm doing well, thanks!", tool_calls=[
        {"id": "tu_1", "name": "read_file", "input": {"path": "/tmp/test.txt"}},
    ])
    print(f"  添加助手响应后数量: {len(repl.messages)}")

    repl.add_tool_result("file contents here", "tu_1")
    print(f"  添加工具结果后数量: {len(repl.messages)}")

    # 6. 测试消息格式转换
    print("\n--- 消息格式转换 ---")
    openai_msgs = repl._messages_to_openai_format()
    assert len(openai_msgs) == len(repl.messages)
    roles = [m["role"] for m in openai_msgs]
    assert roles == ["user", "assistant", "tool"], f"期望 [user, assistant, tool], 得到 {roles}"
    print(f"  OpenAI 格式消息数: {len(openai_msgs)}")
    print(f"  消息角色: {roles}")

    print("\n=== 测试完成 ===")
