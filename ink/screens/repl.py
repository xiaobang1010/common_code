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

from query.engine import QueryEngine, build_engine_config


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
        # 会话级引擎，持有消息历史与 token 用量，跨多次 submitMessage 持久化
        self._engine = QueryEngine(build_engine_config())

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

                # 普通消息 → 调用 LLM，把用户输入文本传给引擎
                if not result.handled:
                    await self._call_llm(user_input)

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

    async def _call_llm(self, prompt: str) -> None:
        """调用 LLM 并流式输出响应，支持工具调用循环。

        通过 self._engine.submitMessage 提交用户输入，引擎内部持有
        完整消息历史并跨轮持久化。REPL 仅负责构建 user_context /
        system_context 并传入引擎，同时消费 yield 的事件把 assistant
        消息、tool 结果、压缩产物同步回 self._messages 供 UI 渲染。
        """
        from query.services.api.llm import StreamEvent
        from query.utils.messages import is_compact_boundary_message
        from startup.utils.context import get_user_context, get_system_context

        # 构建上下文（对齐 TS 版：由调用方构建后传入引擎）
        user_context = get_user_context()
        system_context = get_system_context()

        # 引擎内部维护历史，这里只传 prompt；通过 yield 事件同步到 UI 消息列表
        async for event in self._engine.submitMessage(
            prompt, user_context=user_context, system_context=system_context
        ):
            if isinstance(event, StreamEvent):
                self._handle_stream_event(event)
            elif isinstance(event, dict):
                self._handle_yielded_message(event, is_compact_boundary_message)

    def _handle_stream_event(self, event: "StreamEvent") -> None:
        """处理 query loop yield 的 StreamEvent。

        content → 流式打印
        tool_call_delta → 忽略（query loop 内部已聚合执行）
        usage → 更新 token 计数
        error → 打印错误
        done → 换行
        """
        if event.type == "content":
            # query loop 首个 content="" 是 turn-start 信号，跳过空内容
            if event.content:
                print(event.content, end="", flush=True)
        elif event.type == "usage" and event.usage:
            state = self._app_state.get_state()
            if event.usage.get("prompt_tokens"):
                state.token_usage.input_tokens = event.usage["prompt_tokens"]
            if event.usage.get("completion_tokens"):
                state.token_usage.output_tokens = event.usage["completion_tokens"]
        elif event.type == "error":
            print(f"\nError: {event.content}")
        elif event.type == "done":
            print()  # 换行

    def _handle_yielded_message(
        self,
        msg: dict,
        is_boundary_fn,
    ) -> None:
        """处理 query loop yield 的 dict 消息。

        compact boundary → 替换完整历史（对齐 TS setMessages(() => [newMessage])）
        assistant / tool → append 到历史
        压缩产物中的 summary 等普通消息 → append
        """
        if is_boundary_fn(msg):
            # 收到 boundary marker → 压缩发生了，替换完整历史
            # query loop 会继续 yield summary 等后续消息，它们会正常 append
            self._messages.clear()
            self._messages.append(MessageData(
                role=msg.get("role", "system"),
                content=msg.get("content", ""),
                is_compact_boundary=True,
            ))
        else:
            # assistant 消息（含 tool_calls）或 tool 结果消息 → append
            self._messages.append(MessageData(
                role=msg.get("role", "user"),
                content=msg.get("content", ""),
                tool_calls=msg.get("tool_calls"),
                tool_call_id=msg.get("tool_call_id"),
            ))

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
