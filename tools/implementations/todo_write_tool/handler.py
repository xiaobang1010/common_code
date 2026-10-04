"""TodoWrite 实现：结构化清单 → .agent/todos/<name>.md 落盘。

与 agent-todos-suite 的文件约定完全一致：平铺单文件、`- [ ] ` / `- [x] ` 前缀行。
归属与进展面板复用现有链路（notify_file_changed → 归属记录 → /api/spec/progress）。
"""

from __future__ import annotations

from typing import Any

import asyncio
import re

from server.file_events import notify_file_changed
from tools.implementations.runtime.errors import ToolExecutionError
from tools.implementations.runtime.paths import resolve_workspace_path
from tools.implementations.todo_write_tool.schema import TodoWriteInput

# 清单目录（相对工作区），与 server/routers/spec/routes.py 的 TODOS_DIR 保持一致
TODOS_DIR = ".agent/todos"

# 名字合法性：与归属识别的 TODO_REF_PATTERN 对齐（不含分隔符/引号/空白/冒号）
_NAME_RE = re.compile(r"^[^/\\\"'\s:]+$")


def validate_name(name: str) -> str:
    """清洗并校验清单名：去 .md 后缀与首尾空白，其余字符必须合法。"""
    cleaned = name.strip()
    if cleaned.lower().endswith(".md"):
        cleaned = cleaned[:-3]
    if not cleaned or not _NAME_RE.match(cleaned):
        raise ValueError(
            f"清单名不合法：{name!r}（需 kebab-case，不含路径分隔符/引号/空白/冒号）"
        )
    return cleaned


def render_todo_markdown(inp: TodoWriteInput) -> str:
    """把结构化清单渲染为 checkbox markdown。进行中的项放在最前便于一眼看到。"""
    ordered = sorted(
        inp.todos, key=lambda t: 0 if t.status == "in_progress" else 1
    )
    lines = []
    for item in ordered:
        mark = "x" if item.status == "completed" else " "
        suffix = "（进行中）" if item.status == "in_progress" else ""
        lines.append(f"- [{mark}] {item.content}{suffix}")
    return "\n".join(lines) + "\n"


async def handle_todo_write(inp: TodoWriteInput) -> dict[str, Any]:
    """全量写入清单文件并广播变更。

    返回字段：path（相对工作区）、total、done。
    """
    try:
        name = validate_name(inp.name)
    except ValueError as exc:
        raise ToolExecutionError("invalid_name", str(exc)) from exc

    if not inp.todos:
        raise ToolExecutionError(
            "empty_todos",
            "todos 不能为空：要结束任务请把所有条目置为 completed，而不是传空清单",
        )

    root = resolve_workspace_path(".")
    todo_dir = root / TODOS_DIR
    todo_path = todo_dir / f"{name}.md"

    def _write_sync() -> tuple[int, int]:
        todo_dir.mkdir(parents=True, exist_ok=True)
        text = render_todo_markdown(inp)
        todo_path.write_text(text, encoding="utf-8")
        stat = todo_path.stat()
        return int(stat.st_mtime), stat.st_size

    mtime, size = await asyncio.to_thread(_write_sync)

    # 广播文件变更并记录会话归属（进展面板据此实时刷新）
    notify_file_changed(str(todo_path), "write", mtime, size)

    done = sum(1 for t in inp.todos if t.status == "completed")
    return {"path": f"{TODOS_DIR}/{name}.md", "total": len(inp.todos), "done": done}


def format_model_content(structured: dict[str, Any]) -> str:
    """把结构化结果拼成给模型的文本。"""
    return (
        f"清单已更新：{structured['path']}（{structured['done']}/{structured['total']} 已完成）。"
        "做完一项立即更新对应条目状态。"
    )
