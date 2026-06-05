"""多模式输入组件。

参考原始 TypeScript 实现: src/components/TextInput.tsx, src/components/PromptInput/PromptInput.tsx

使用 prompt_toolkit 实现交互式输入，支持：
- 语法高亮（斜杠命令）
- 历史搜索（Ctrl+R）
- 自动补全（斜杠命令 + 文件路径）
- 多行模式（Shift+Enter 换行，Enter 提交）
"""

from .core import (
    InputMode,
    PromptInput,
    SlashCommandCompleter,
)

__all__ = [
    "InputMode",
    "PromptInput",
    "SlashCommandCompleter",
]
