"""Task 工具族 — TaskCreate/TaskUpdate/TaskList/TaskGet。

任务持久化到 ~/.agent/tasks/{team}/ 目录，团队成员可创建、认领、更新、查询任务。
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from tools.protocol import Tool, ToolResult, ToolUseContext, build_tool

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 任务数据结构
# ---------------------------------------------------------------------------

# 任务状态
TASK_STATUSES = {"pending", "in_progress", "completed", "blocked"}

# 任务优先级
TASK_PRIORITIES = {"high", "medium", "low"}


def _get_tasks_dir(team_name: str) -> Path:
    """获取任务目录。"""
    from tools.team.manager import _get_tasks_dir as _td
    return _td(team_name)


def _get_task_file(team_name: str, task_id: int) -> Path:
    """获取任务文件路径。"""
    return _get_tasks_dir(team_name) / f"task_{task_id}.json"


def _next_task_id(team_name: str) -> int:
    """获取下一个任务 ID。"""
    tasks_dir = _get_tasks_dir(team_name)
    if not tasks_dir.exists():
        return 1
    existing = list(tasks_dir.glob("task_*.json"))
    if not existing:
        return 1
    ids = []
    for f in existing:
        try:
            ids.append(int(f.stem.split("_")[1]))
        except (IndexError, ValueError):
            pass
    return max(ids) + 1 if ids else 1


def _create_task_record(
    team_name: str,
    title: str,
    description: str = "",
    owner: str | None = None,
    priority: str = "medium",
) -> dict[str, Any]:
    """创建任务记录并持久化。"""
    task_id = _next_task_id(team_name)
    task = {
        "id": task_id,
        "title": title,
        "description": description,
        "owner": owner,
        "status": "pending",
        "priority": priority if priority in TASK_PRIORITIES else "medium",
        "blocks": [],          # 本任务阻塞哪些任务（id 列表）
        "blocked_by": [],      # 本任务被哪些任务阻塞（id 列表）
        "created_at": time.time(),
        "updated_at": time.time(),
    }
    task_file = _get_task_file(team_name, task_id)
    task_file.parent.mkdir(parents=True, exist_ok=True)
    task_file.write_text(
        json.dumps(task, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return task


def _read_task(team_name: str, task_id: int) -> dict[str, Any] | None:
    """读取单个任务。"""
    task_file = _get_task_file(team_name, task_id)
    if not task_file.exists():
        return None
    return json.loads(task_file.read_text(encoding="utf-8"))


def _write_task(team_name: str, task_id: int, task: dict[str, Any]) -> None:
    """写入任务。"""
    task["updated_at"] = time.time()
    task_file = _get_task_file(team_name, task_id)
    task_file.write_text(
        json.dumps(task, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _list_all_tasks(team_name: str) -> list[dict[str, Any]]:
    """列出团队所有任务。"""
    tasks_dir = _get_tasks_dir(team_name)
    if not tasks_dir.exists():
        return []
    tasks = []
    for f in sorted(tasks_dir.glob("task_*.json")):
        try:
            tasks.append(json.loads(f.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, ValueError):
            pass
    return tasks


# ---------------------------------------------------------------------------
# TaskCreate 工具
# ---------------------------------------------------------------------------


class TaskCreateInput(BaseModel):
    """TaskCreate 工具输入。"""

    team_name: str
    title: str
    description: str = ""
    priority: str = "medium"


async def _task_create_execute(inp: TaskCreateInput, _ctx: ToolUseContext) -> ToolResult:
    """创建任务。"""
    from tools.team.manager import team_exists

    if not team_exists(inp.team_name):
        return ToolResult(
            content=f"Team not found: {inp.team_name}",
            is_error=True,
        )

    task = _create_task_record(
        inp.team_name, inp.title, inp.description, priority=inp.priority,
    )
    return ToolResult(
        content=f"Task created: #{task['id']} - {task['title']}",
        metadata={"task_id": task["id"]},
    )


def get_task_create_tool() -> Tool:
    """返回 TaskCreate 工具实例。"""
    return build_tool(
        name="TaskCreate",
        description="Create a task in a team's shared task list",
        input_schema=TaskCreateInput,
        execute=_task_create_execute,
        prompt="在团队共享任务列表中创建新任务",
        is_read_only=True,
    )


# ---------------------------------------------------------------------------
# TaskUpdate 工具
# ---------------------------------------------------------------------------


class TaskUpdateInput(BaseModel):
    """TaskUpdate 工具输入。"""

    team_name: str
    task_id: int
    status: str | None = None
    owner: str | None = None
    title: str | None = None
    description: str | None = None
    priority: str | None = None
    add_blocks: list[int] | None = None
    add_blocked_by: list[int] | None = None


async def _task_update_execute(inp: TaskUpdateInput, _ctx: ToolUseContext) -> ToolResult:
    """更新任务。"""
    task = _read_task(inp.team_name, inp.task_id)
    if task is None:
        return ToolResult(
            content=f"Task not found: #{inp.task_id}",
            is_error=True,
        )

    if inp.status is not None:
        if inp.status not in TASK_STATUSES:
            return ToolResult(
                content=f"Invalid status: {inp.status}. Valid: {TASK_STATUSES}",
                is_error=True,
            )
        task["status"] = inp.status
    if inp.owner is not None:
        task["owner"] = inp.owner
    if inp.title is not None:
        task["title"] = inp.title
    if inp.description is not None:
        task["description"] = inp.description
    if inp.priority is not None:
        task["priority"] = inp.priority

    # 任务依赖双向维护
    if inp.add_blocks is not None:
        for blocked_id in inp.add_blocks:
            if blocked_id not in task["blocks"]:
                task["blocks"].append(blocked_id)
            # 同步更新被阻塞任务的 blocked_by
            blocked_task = _read_task(inp.team_name, blocked_id)
            if blocked_task is not None:
                if inp.task_id not in blocked_task["blocked_by"]:
                    blocked_task["blocked_by"].append(inp.task_id)
                _write_task(inp.team_name, blocked_id, blocked_task)

    if inp.add_blocked_by is not None:
        for blocker_id in inp.add_blocked_by:
            if blocker_id not in task["blocked_by"]:
                task["blocked_by"].append(blocker_id)
            # 同步更新阻塞任务的 blocks
            blocker_task = _read_task(inp.team_name, blocker_id)
            if blocker_task is not None:
                if inp.task_id not in blocker_task["blocks"]:
                    blocker_task["blocks"].append(inp.task_id)
                _write_task(inp.team_name, blocker_id, blocker_task)

    _write_task(inp.team_name, inp.task_id, task)

    return ToolResult(
        content=f"Task #{inp.task_id} updated: {task['status']}",
        metadata={"task_id": inp.task_id, "status": task["status"]},
    )


def get_task_update_tool() -> Tool:
    """返回 TaskUpdate 工具实例。"""
    return build_tool(
        name="TaskUpdate",
        description="Update a task's status, owner, or other fields",
        input_schema=TaskUpdateInput,
        execute=_task_update_execute,
        prompt="更新团队任务的状态/owner/标题等",
        is_read_only=True,
    )


# ---------------------------------------------------------------------------
# TaskList 工具
# ---------------------------------------------------------------------------


class TaskListInput(BaseModel):
    """TaskList 工具输入。"""

    team_name: str
    status: str | None = None
    owner: str | None = None


async def _task_list_execute(inp: TaskListInput, _ctx: ToolUseContext) -> ToolResult:
    """列出团队任务。"""
    tasks = _list_all_tasks(inp.team_name)

    # 过滤
    if inp.status:
        tasks = [t for t in tasks if t.get("status") == inp.status]
    if inp.owner:
        tasks = [t for t in tasks if t.get("owner") == inp.owner]

    if not tasks:
        return ToolResult(content="No tasks found")

    lines = []
    for t in tasks:
        owner = t.get("owner") or "unassigned"
        lines.append(f"#{t['id']} [{t['status']}] {t['title']} (owner: {owner})")

    return ToolResult(content="\n".join(lines))


def get_task_list_tool() -> Tool:
    """返回 TaskList 工具实例。"""
    return build_tool(
        name="TaskList",
        description="List tasks in a team, optionally filtered by status/owner",
        input_schema=TaskListInput,
        execute=_task_list_execute,
        prompt="列出团队任务列表，可按状态/owner过滤",
        is_read_only=True,
    )


# ---------------------------------------------------------------------------
# TaskGet 工具
# ---------------------------------------------------------------------------


class TaskGetInput(BaseModel):
    """TaskGet 工具输入。"""

    team_name: str
    task_id: int


async def _task_get_execute(inp: TaskGetInput, _ctx: ToolUseContext) -> ToolResult:
    """查询单个任务详情。"""
    task = _read_task(inp.team_name, inp.task_id)
    if task is None:
        return ToolResult(
            content=f"Task not found: #{inp.task_id}",
            is_error=True,
        )

    lines = [
        f"Task #{task['id']}: {task['title']}",
        f"  Status: {task['status']}",
        f"  Owner: {task.get('owner') or 'unassigned'}",
        f"  Priority: {task.get('priority', 'medium')}",
    ]
    if task.get("description"):
        lines.append(f"  Description: {task['description']}")

    return ToolResult(content="\n".join(lines))


def get_task_get_tool() -> Tool:
    """返回 TaskGet 工具实例。"""
    return build_tool(
        name="TaskGet",
        description="Get details of a single task",
        input_schema=TaskGetInput,
        execute=_task_get_execute,
        prompt="查询单个任务详情",
        is_read_only=True,
    )


# ---------------------------------------------------------------------------
# get_all_task_tools — 获取所有 Task 工具
# ---------------------------------------------------------------------------


def get_all_task_tools() -> list[Tool]:
    """返回全部 Task 工具列表。"""
    return [
        get_task_create_tool(),
        get_task_update_tool(),
        get_task_list_tool(),
        get_task_get_tool(),
    ]
