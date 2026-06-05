"""命令上下文 — 传递给斜杠命令处理函数的运行时环境。

参考原始 TypeScript 实现 src/commands/ 中各命令接收的 context 对象。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from startup.state.app_state import AppStateProvider
    from query.config import QueryConfig


# ---------------------------------------------------------------------------
# CommandContext — 命令处理函数的运行时上下文
# ---------------------------------------------------------------------------


@dataclass
class CommandContext:
    """斜杠命令处理函数接收的上下文对象。

    Attributes:
        messages: 当前消息列表（OpenAI 格式 dict 列表）
        app_state: 应用状态提供者
        config: 查询配置快照（可能为 None）
        compact_fn: 压缩函数（async def compact(messages, model) -> list[dict]）
        repl: REPL 屏幕引用，用于直接操作 REPL 状态
        project_root: 项目根目录路径，用于定位 spec 目录（.agent/specs/）等
        args: 命令附加参数（如 /compact [instructions] 中的 instructions）
    """

    messages: list[dict] = field(default_factory=list)
    app_state: Any = None  # AppStateProvider
    config: QueryConfig | None = None
    compact_fn: Callable | None = None
    repl: Any = None  # REPLScreen
    project_root: str | None = None
    args: str = ""
