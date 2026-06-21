"""多模式输入组件核心实现。

参考原始 TypeScript 实现: src/components/PromptInput/PromptInput.tsx

使用 prompt_toolkit 实现交互式输入。
"""

from __future__ import annotations

import enum
import os
from pathlib import Path
from typing import Optional

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.history import FileHistory, InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.lexers import Lexer
from prompt_toolkit.document import Document


# ---------------------------------------------------------------------------
# InputMode — 输入模式枚举
# ---------------------------------------------------------------------------

class InputMode(enum.Enum):
    """输入模式。"""
    SINGLE_LINE = "single_line"
    MULTI_LINE = "multi_line"


# ---------------------------------------------------------------------------
# SlashCommandCompleter — 斜杠命令自动补全
# ---------------------------------------------------------------------------

# 内置斜杠命令列表
SLASH_COMMANDS = [
    ("/help", "Show help information"),
    ("/clear", "Clear conversation history"),
    ("/compact", "Compact conversation context"),
    ("/config", "Open configuration"),
    ("/model", "Switch model"),
    ("/cost", "Show cost information"),
    ("/exit", "Exit the application"),
]


class SlashCommandCompleter(Completer):
    """斜杠命令自动补全器。

    支持斜杠命令补全和文件路径补全。
    """

    def __init__(
        self,
        commands: Optional[list[tuple[str, str]]] = None,
        enable_path_completion: bool = True,
    ) -> None:
        self._commands = commands or SLASH_COMMANDS
        self._enable_path_completion = enable_path_completion

    def get_completions(self, document: Document, complete_event):
        """获取补全建议。"""
        text = document.text_before_cursor

        # 斜杠命令补全
        if text.startswith("/"):
            word = text.lstrip("/")
            for cmd, desc in self._commands:
                cmd_name = cmd.lstrip("/")
                if cmd_name.startswith(word) and word != cmd_name:
                    # 计算要删除的文本长度（从 / 之后开始）
                    yield Completion(
                        cmd_name,
                        start_position=-len(word),
                        display=f"{cmd}  {desc}",
                        display_meta=desc,
                    )
            return

        # 文件路径补全（以 ./ 或 / 开头时触发）
        if self._enable_path_completion and (text.endswith(" ") or not text):
            return

        if self._enable_path_completion:
            # 尝试提取最后一个空格后的词作为路径
            last_word = text.rsplit(" ", 1)[-1] if " " in text else text
            if last_word.startswith(("./", "../", "/")) or (
                len(last_word) > 1 and last_word[0] == "~"
            ):
                # 展开路径
                try:
                    expanded = os.path.expanduser(last_word)
                    parent = os.path.dirname(expanded)
                    prefix = os.path.basename(expanded)

                    if os.path.isdir(parent):
                        for entry in os.listdir(parent):
                            if entry.startswith(prefix):
                                full_path = os.path.join(parent, entry)
                                display = os.path.join(
                                    os.path.dirname(last_word), entry
                                )
                                is_dir = os.path.isdir(full_path)
                                yield Completion(
                                    display + ("/" if is_dir else ""),
                                    start_position=-len(last_word),
                                    display=display + ("/" if is_dir else ""),
                                    display_meta="dir" if is_dir else "file",
                                )
                except (OSError, ValueError):
                    pass


# ---------------------------------------------------------------------------
# SlashCommandLexer — 斜杠命令语法高亮
# ---------------------------------------------------------------------------

class SlashCommandLexer(Lexer):
    """斜杠命令语法高亮。

    以 / 开头的行使用特殊颜色。
    """

    def lex_document(self, document: Document):
        lines = document.lines

        def get_line(lineno: int):
            line = lines[lineno] if lineno < len(lines) else ""
            fragments = []

            if line.startswith("/"):
                # 斜杠命令：命令部分用粗体，参数部分用普通样式
                parts = line.split(" ", 1)
                fragments.append(("class:command", parts[0]))
                if len(parts) > 1:
                    fragments.append(("", " " + parts[1]))
            else:
                fragments.append(("", line))

            return fragments

        return get_line


# ---------------------------------------------------------------------------
# PromptInput — 多模式输入组件
# ---------------------------------------------------------------------------

class PromptInput:
    """多模式输入组件。

    使用 prompt_toolkit 实现交互式输入，支持：
    - 语法高亮（斜杠命令）
    - 历史搜索（Ctrl+R）
    - 自动补全（斜杠命令 + 文件路径）
    - 多行模式（Shift+Enter 换行，Enter 提交）

    Attributes:
        multiline: 是否为多行模式
        history: 历史记录
    """

    def __init__(
        self,
        multiline: bool = False,
        history_file: Optional[str] = None,
    ) -> None:
        self._multiline = multiline
        self._mode = InputMode.MULTI_LINE if multiline else InputMode.SINGLE_LINE
        self._history_items: list[str] = []

        # 创建 prompt_toolkit 历史记录
        if history_file:
            self._pt_history = FileHistory(history_file)
        else:
            self._pt_history = InMemoryHistory()

        # 创建补全器
        self._completer = SlashCommandCompleter()

        # 创建语法高亮
        self._lexer = SlashCommandLexer()

        # 创建键绑定
        self._key_bindings = self._create_key_bindings()

        # 创建 PromptSession
        self._session: Optional[PromptSession] = None

    def _create_key_bindings(self) -> KeyBindings:
        """创建键绑定。"""
        kb = KeyBindings()

        if self._multiline:
            # 多行模式：Enter 提交，Alt+Enter 换行
            @kb.add("escape", "enter")
            def _newline(event: object) -> None:
                event.current_buffer.insert_text("\n")

        return kb

    def _get_session(self) -> PromptSession:
        """获取或创建 PromptSession。"""
        if self._session is None:
            self._session = PromptSession(
                history=self._pt_history,
                completer=self._completer,
                lexer=self._lexer,
                multiline=self._multiline,
                key_bindings=self._key_bindings,
                enable_history_search=True,
            )
        return self._session

    @property
    def mode(self) -> InputMode:
        """当前输入模式。"""
        return self._mode

    def get_input(self, prompt: str = "> ") -> str:
        """获取用户输入。

        Args:
            prompt: 提示符文本

        Returns:
            用户输入的文本
        """
        session = self._get_session()

        if self._multiline:
            # 多行模式：使用 prompt_toolkit 的多行输入
            result = session.prompt(
                prompt,
                multiline=True,
                prompt_continuation="... ",
            )
        else:
            # 单行模式
            result = session.prompt(prompt)

        return result.strip()

    def set_history(self, items: list[str]) -> None:
        """设置历史记录。

        Args:
            items: 历史记录列表
        """
        self._history_items = list(items)
        # 重建 InMemoryHistory
        self._pt_history = InMemoryHistory()
        for item in items:
            self._pt_history.append_string(item)
        # 重置 session 以使用新历史
        self._session = None

    def add_to_history(self, item: str) -> None:
        """添加历史记录。

        Args:
            item: 要添加的历史记录项
        """
        if not item.strip():
            return
        self._history_items.append(item)
        self._pt_history.append_string(item)
