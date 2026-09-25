"""TodoWrite 工具测试：落盘格式、归属广播、非法输入拒绝。"""

from __future__ import annotations

import pytest

from tools.implementations.runtime.errors import ToolExecutionError
from tools.implementations.todo_write_tool.handler import (
    format_model_content,
    handle_todo_write,
)
from tools.implementations.todo_write_tool.schema import TodoItem, TodoWriteInput
from tools.implementations.todo_write_tool.tool import get_todo_write_tool
from tools.utils.schema import tool_to_openai_schema


@pytest.mark.asyncio
async def test_writes_checklist_file(workspace):
    """写入 .agent/todos/<name>.md，格式与 spec 进展解析兼容。"""
    r = await handle_todo_write(
        TodoWriteInput(
            name="fix-bug",
            todos=[
                TodoItem(content="定位问题", status="completed"),
                TodoItem(content="修复", status="in_progress"),
                TodoItem(content="补测试", status="pending"),
            ],
        )
    )
    assert r == {"path": ".agent/todos/fix-bug.md", "total": 3, "done": 1}

    text = (workspace / ".agent" / "todos" / "fix-bug.md").read_text(encoding="utf-8")
    # 与 _parse_checklist 的前缀约定一致：`- [x] ` / `- [ ] `
    assert "- [x] 定位问题" in text
    assert "- [ ] 补测试" in text
    # 进行中项排最前，带（进行中）标记
    assert text.splitlines()[0].startswith("- [ ] 修复")


@pytest.mark.asyncio
async def test_notify_broadcast(workspace, monkeypatch):
    """写盘成功必须触发 notify_file_changed（归属 + file_changed 广播的前提）。"""
    seen: list[tuple] = []
    import tools.implementations.todo_write_tool.handler as handler

    monkeypatch.setattr(
        handler,
        "notify_file_changed",
        lambda path, change, mtime, size: seen.append((path, change, mtime, size)),
    )
    await handle_todo_write(
        TodoWriteInput(name="t1", todos=[TodoItem(content="a", status="pending")])
    )
    assert len(seen) == 1
    path, change, _, size = seen[0]
    assert change == "write"
    assert path.endswith("t1.md")
    assert size > 0


@pytest.mark.asyncio
async def test_rejects_bad_name_and_empty_todos(workspace):
    with pytest.raises(ToolExecutionError) as e1:
        await handle_todo_write(
            TodoWriteInput(name="bad/name", todos=[TodoItem(content="a", status="pending")])
        )
    assert e1.value.code == "invalid_name"

    with pytest.raises(ToolExecutionError) as e2:
        await handle_todo_write(TodoWriteInput(name="ok", todos=[]))
    assert e2.value.code == "empty_todos"


def test_todo_write_registered_and_described():
    tool = get_todo_write_tool()
    schema = tool_to_openai_schema(tool)
    assert schema["function"]["description"] == tool.prompt
    assert "TodoWrite" in [t.name for t in __import__("tools").get_tools()]
    assert format_model_content({"path": "p", "total": 2, "done": 1}).startswith("清单已更新")
