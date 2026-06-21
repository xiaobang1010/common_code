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
from ink.ui.message import render_message, render_tool_call_summary
from ink.ui.prompt_input import PromptInput, InputMode
from ink.renderer import RenderNode, LayoutInfo

from tools.commands.commands import find_command, get_commands
from tools.commands.commands_context import CommandContext

from query.engine import QueryEngine, build_engine_config
from query.services.pricing import calculate_cost
from startup.bootstrap.state import add_to_total_cost

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from ink.core import Ink
    from query.services.api.llm import StreamEvent
    from query.loop import LoopResult


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

    def __init__(self, app_state: AppStateProvider, ink: Optional["Ink"] = None) -> None:
        self._app_state = app_state
        self._ink = ink
        self._screen: ScreenMode = ScreenMode.PROMPT
        self._messages: list[MessageData] = []
        self._prompt_input = PromptInput(multiline=False)
        self._is_running = False
        self._on_submit: Optional[Callable[[str], Any]] = None
        # 流式输出缓冲区，用于 Ink 增量渲染
        self._stream_buffer: str = ""
        # 会话级引擎，持有消息历史与 token 用量，跨多次 submitMessage 持久化
        # 注入权限弹窗回调：ASK 决策时调 show_permission_dialog 让用户选择
        self._engine = QueryEngine(build_engine_config(
            permission_prompt=self._permission_prompt,
        ))

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

                # Ink 模式：input 前重置帧缓存，让 prompt_toolkit 接管屏幕
                if self._ink is not None:
                    self._ink.clear()

                # 渲染提示符并获取输入
                user_input = await asyncio.to_thread(
                    self._prompt_input.get_input, "> "
                )

                # Ink 模式：input 后重置帧缓存（prompt_toolkit 改了屏幕内容）
                if self._ink is not None:
                    self._ink.clear()

                if not user_input:
                    continue

                # 处理提交
                result = self.handle_prompt_submit(user_input)

                if result.should_exit:
                    self._is_running = False
                    break

                if result.output:
                    self._emit(result.output)
                    continue

                # 普通消息 → 调用 LLM，把用户输入文本传给引擎
                if not result.handled:
                    await self._call_llm(user_input)

            except (EOFError, KeyboardInterrupt):
                self._emit("\nGoodbye!")
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
        from query.loop import LoopResult
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
            elif isinstance(event, LoopResult):
                self._handle_loop_result(event)

    def _handle_stream_event(self, event: "StreamEvent") -> None:
        """处理 query loop yield 的 StreamEvent。

        content → 流式输出（Ink 模式累积到 _stream_buffer 增量渲染）
        tool_call_delta → 忽略（query loop 内部已聚合执行）
        usage → 更新 token 计数
        error → 打印错误
        done → 流式结束，清空缓冲区
        """
        if event.type == "content":
            # query loop 首个 content="" 是 turn-start 信号，跳过空内容
            if event.content:
                if self._ink is not None:
                    # Ink 模式：累积到缓冲区，增量渲染（帧差分只更新最后一行）
                    self._stream_buffer += event.content
                    self._flush_ink()
                else:
                    print(event.content, end="", flush=True)
        elif event.type == "usage" and event.usage:
            state = self._app_state.get_state()
            # token 用量累加（不是覆盖），跨多轮累计
            prompt_tokens = event.usage.get("prompt_tokens", 0)
            completion_tokens = event.usage.get("completion_tokens", 0)
            cache_read = event.usage.get("cache_read_input_tokens", 0)
            cache_creation = event.usage.get("cache_creation_input_tokens", 0)
            state.token_usage.input_tokens += prompt_tokens
            state.token_usage.output_tokens += completion_tokens
            state.token_usage.cache_read_input_tokens += cache_read
            state.token_usage.cache_creation_input_tokens += cache_creation
            # 按模型定价算本次成本，累加到 AppState
            cost = calculate_cost(state.model or "", event.usage)
            state.total_cost_usd += cost
            # 同步写入 bootstrap state（model_usage 用 input/output_tokens 键名）
            add_to_total_cost(
                cost,
                {
                    "input_tokens": prompt_tokens,
                    "output_tokens": completion_tokens,
                },
                state.model or "",
            )
        elif event.type == "error":
            self._emit(f"Error: {event.content}")
        elif event.type == "done":
            # 流式结束：清空缓冲区（完整 assistant 消息会通过 _handle_yielded_message 加入）
            if self._ink is not None:
                self._stream_buffer = ""
            else:
                print()  # 换行

    def _handle_yielded_message(
        self,
        msg: dict,
        is_boundary_fn,
    ) -> None:
        """处理 query loop yield 的 dict 消息。

        compact boundary → 替换完整历史（对齐 TS setMessages(() => [newMessage])），
        清屏后渲染 boundary 标记
        assistant / tool → append 到历史并渲染新增消息
        压缩产物中的 summary 等普通消息 → append
        """
        width = self._get_terminal_width()
        if is_boundary_fn(msg):
            # 收到 boundary marker → 压缩发生了，替换完整历史
            # query loop 会继续 yield summary 等后续消息，它们会正常 append
            self._messages.clear()
            boundary_msg = MessageData(
                role=msg.get("role", "system"),
                content=msg.get("content", ""),
                is_compact_boundary=True,
            )
            self._messages.append(boundary_msg)
            if self._ink is not None:
                # Ink 模式：清空流式缓冲区并全量刷新
                self._stream_buffer = ""
                self._flush_ink()
            else:
                # 降级模式：清屏并渲染 boundary 分隔符
                print("\033[2J\033[H", end="")
                print(render_message(boundary_msg, width=width))
        else:
            # assistant 消息（含 tool_calls）或 tool 结果消息 → append
            msg_data = MessageData(
                role=msg.get("role", "user"),
                content=msg.get("content", ""),
                tool_calls=msg.get("tool_calls"),
                tool_call_id=msg.get("tool_call_id"),
            )
            self._messages.append(msg_data)
            if self._ink is not None:
                # Ink 模式：清空流式缓冲区（完整消息已加入列表），全量刷新
                self._stream_buffer = ""
                self._flush_ink()
            else:
                # 降级模式：渲染新增消息
                if msg_data.role == "assistant" and msg_data.tool_calls:
                    # 有工具调用的 assistant：content 已流式打印，只渲染工具调用摘要
                    for tc in msg_data.tool_calls:
                        print(f"  {render_tool_call_summary(tc)}")
                elif msg_data.role == "tool":
                    # 工具结果：渲染预览（◈ 绿色前缀）
                    print(render_message(msg_data, width=width))
                # 纯文本 assistant（无 tool_calls）：content 已流式打印，不重复渲染

    def _handle_loop_result(self, result: "LoopResult") -> None:
        """处理 query loop yield 的 LoopResult，按退出原因给用户反馈。

        - completed → 流式输出已自然结束，仅换行收尾
        - prompt_too_long → 上下文超限且压缩未能恢复
        - model_error → 模型调用异常
        - max_output_tokens_exhausted → 输出 token 恢复次数用尽
        - 其他 → 兜底打印 reason
        """
        if result.reason == "completed":
            # 流式输出已自然结束；Ink 模式下缓冲区已在 done 事件清空
            if self._ink is None:
                print()  # 降级模式换行收尾
        elif result.reason == "prompt_too_long":
            self._emit("上下文过长且压缩未能恢复，请用 /compact 手动压缩或清理会话")
        elif result.reason == "model_error":
            self._emit(f"模型调用出错：{result.error}")
        elif result.reason == "max_output_tokens_exhausted":
            self._emit("输出 token 已用尽，请缩减请求或用 /compact 压缩历史")
        else:
            self._emit(f"循环退出：{result.reason}")

    async def _permission_prompt(
        self, tool_name: str, tool_input: dict, reason: str
    ) -> str:
        """权限确认弹窗回调，包装 show_permission_dialog。

        executor 收到 ASK 决策时调用此回调，返回 "allow"/"deny"/"always_allow"。
        show_permission_dialog 返回 PermissionDecision 枚举，其 value 正是这些字符串。
        """
        from ink.ui.permission_dialog import show_permission_dialog
        decision = await show_permission_dialog(tool_name, tool_input, reason)
        return decision.value

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

        # 渲染新增的用户消息（▸ 蓝色前缀）
        if self._ink is not None:
            self._flush_ink()
        else:
            width = self._get_terminal_width()
            print(render_message(user_msg, width=width))

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

        内容感知：数量或任一条目的关键字段变化时才重建，
        重建时从旧消息迁移 uuid/timestamp/is_compact_boundary 等元数据。
        """
        # 数量不同，直接重建
        if len(openai_messages) != len(self._messages):
            self._rebuild_messages_from_openai(openai_messages)
            return
        # 数量相同，逐条比较关键字段
        for new_dict, old_msg in zip(openai_messages, self._messages):
            if (new_dict.get("role") != old_msg.role or
                    new_dict.get("content") != old_msg.content or
                    new_dict.get("tool_calls") != old_msg.tool_calls or
                    new_dict.get("tool_call_id") != old_msg.tool_call_id):
                self._rebuild_messages_from_openai(openai_messages)
                return
        # 内容完全一致，不重建

    def _rebuild_messages_from_openai(self, openai_messages: list[dict]) -> None:
        """根据 OpenAI 格式消息重建内部列表，并迁移旧消息的元数据。"""
        new_list: list[MessageData] = []
        for i, d in enumerate(openai_messages):
            new_msg = MessageData(
                role=d.get("role", "user"),
                content=d.get("content", ""),
                tool_calls=d.get("tool_calls"),
                tool_call_id=d.get("tool_call_id"),
            )
            # 从旧消息迁移元数据（如果该索引存在旧消息）
            if i < len(self._messages):
                old = self._messages[i]
                new_msg.uuid = old.uuid
                new_msg.timestamp = old.timestamp
                # compact boundary 信息只存在于内部结构，OpenAI dict 里没有，需要从旧消息保留
                new_msg.is_compact_boundary = old.is_compact_boundary
            new_list.append(new_msg)
        self._messages.clear()
        self._messages.extend(new_list)

    # -----------------------------------------------------------------------
    # Ink 渲染辅助
    # -----------------------------------------------------------------------

    def _build_status_text(self) -> str:
        """构建状态栏纯文本（不含 ANSI 颜色，颜色由 Ink props 处理）。"""
        state = self._app_state.get_state()
        parts: list[str] = []
        if state.model:
            parts.append(state.model)
        usage = state.token_usage
        if usage.input_tokens or usage.output_tokens:
            parts.append(f"tokens: {usage.input_tokens}in/{usage.output_tokens}out")
        if state.total_cost_usd > 0:
            parts.append(f"cost: ${state.total_cost_usd:.4f}")
        if self._screen == ScreenMode.TRANSCRIPT:
            parts.append("[transcript]")
        return " │ ".join(parts)

    def _build_render_tree(self) -> RenderNode:
        """构造完整屏幕的 RenderNode 树（状态栏 + 消息 + 流式输出）。

        手动设置每个子节点的 layout_info.y 做垂直布局，
        样式通过 props(fg) 传递（Ink 的 Screen 不解析 ANSI 转义）。
        """
        width = self._get_terminal_width()
        children: list[RenderNode] = []
        y = 0

        def add_line(text: str, fg: str = "") -> None:
            nonlocal y
            props: dict = {"children": text}
            if fg:
                props["fg"] = fg
            children.append(RenderNode(
                type="text",
                props=props,
                layout_info=LayoutInfo(x=0, y=y, width=width, height=1),
            ))
            y += 1

        # 状态栏
        status = self._build_status_text()
        if status:
            add_line(status, fg="cyan")
        add_line("")

        # 消息列表
        visible = fold_messages(self._messages, max_visible=50)
        for msg in visible:
            self._add_message_to_tree(msg, add_line)

        # 流式输出（当前 assistant 正在生成的文本）
        if self._stream_buffer:
            add_line(f"● {self._stream_buffer}", fg="white")

        return RenderNode(type="box", children=children)

    def _add_message_to_tree(self, msg: MessageData, add_line) -> None:
        """把单条消息转成 RenderNode 行，通过 add_line 回调添加。"""
        if msg.is_compact_boundary:
            add_line("─ ─ ─  compact boundary  ─ ─ ─", fg="gray")
            return

        prefix_map = {
            "user": ("▸", "blue"),
            "assistant": ("●", "white"),
            "tool": ("◈", "green"),
            "system": ("◆", "gray"),
        }
        prefix, fg = prefix_map.get(msg.role, ("·", "gray"))

        if msg.role == "tool":
            # 工具结果：预览截断 200 字符
            preview = msg.content.strip()
            if len(preview) > 200:
                preview = preview[:197] + "..."
            add_line(f"{prefix} {preview}", fg="green")
        elif msg.role == "assistant" and msg.tool_calls:
            if msg.content:
                add_line(f"{prefix} {msg.content}", fg="white")
            for tc in msg.tool_calls:
                # 工具调用摘要（兼容 OpenAI 和内部格式）
                name = tc.get("name", "")
                if not name and "function" in tc:
                    name = tc["function"].get("name", "unknown")
                if not name:
                    name = "unknown"
                add_line(f"  [Tool: {name}]", fg="cyan")
        else:
            for line in msg.content.split("\n"):
                add_line(f"{prefix} {line}", fg=fg)

    def _flush_ink(self) -> None:
        """用 Ink 渲染当前完整屏幕状态。"""
        if self._ink is None:
            return
        self._ink.render(self._build_render_tree())

    def _emit(self, text: str, role: str = "system") -> None:
        """输出文本。

        Ink 模式：加入消息列表并刷新渲染。
        降级模式：直接 print。
        """
        if self._ink is not None:
            self._messages.append(MessageData(role=role, content=text))
            self._flush_ink()
        else:
            print(text)

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
        if self._ink is not None:
            # Ink 模式：状态栏已包含在完整渲染树中，刷新即可
            self._flush_ink()
            return self._build_status_text()

        # 降级模式：ANSI 颜色 print
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
        self._emit(f"Common Code (model: {model})")
        self._emit("Type /help for available commands.")

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
