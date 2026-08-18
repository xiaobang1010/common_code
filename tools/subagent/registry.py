"""子代理任务注册表（进程级）。

所有前台/后台子代理启动时注册，结束时更新状态与 usage，
为 SendMessage 投递、任务管理 API、并发限制提供统一数据源。
取代旧 resume.py 中仅 resume 路径使用的 _active_subagents 机制。
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from tools.subagent.context import SubagentContext

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 状态与模式常量
# ---------------------------------------------------------------------------

STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_ABORTED = "aborted"
STATUS_STOPPED = "stopped"

MODE_FOREGROUND = "foreground"
MODE_BACKGROUND = "background"

# 终态集合：进入这些状态后任务不再运行
TERMINAL_STATUSES = {STATUS_COMPLETED, STATUS_FAILED, STATUS_ABORTED, STATUS_STOPPED}


# ---------------------------------------------------------------------------
# SubagentTask - 单个子代理的任务记录
# ---------------------------------------------------------------------------


@dataclass
class SubagentTask:
    """子代理任务记录。

    Attributes:
        agent_id: 子代理唯一标识
        session_id: 子代理自身会话标识（当前等于 agent_id）
        parent_session_id: 父会话标识（主对话会话），未知时为 None
        agent_type: 代理类型（如 general-purpose / Explore）
        description: 任务简述
        status: pending/running/completed/failed/aborted/stopped
        mode: foreground / background
        created_at / updated_at: 时间戳（秒）
        output_file: 结果落盘文件路径（截断时才有）
        usage: {"total_tokens": int, "tool_uses": int, "duration_ms": int}
        ctx: 子代理执行上下文（运行期持有，供 SendMessage 入队等）
        task: 后台 asyncio.Task 引用（由注册表持有，防引用丢失）
        error: 失败/终止原因
        final_text: 最终 assistant 文本（完成后填充）
    """

    agent_id: str
    agent_type: str = "general-purpose"
    description: str = ""
    session_id: str = ""
    parent_session_id: str | None = None
    status: str = STATUS_PENDING
    mode: str = MODE_FOREGROUND
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    output_file: str | None = None
    usage: dict[str, int] = field(
        default_factory=lambda: {"total_tokens": 0, "tool_uses": 0, "duration_ms": 0}
    )
    ctx: SubagentContext | None = None
    task: asyncio.Task | None = None
    error: str | None = None
    final_text: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """序列化为 API 友好的字典（不含 ctx/task 等运行期对象）。"""
        return {
            "agent_id": self.agent_id,
            "session_id": self.session_id,
            "parent_session_id": self.parent_session_id,
            "agent_type": self.agent_type,
            "description": self.description,
            "status": self.status,
            "mode": self.mode,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "output_file": self.output_file,
            "usage": dict(self.usage),
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# SubagentTaskRegistry - 注册表
# ---------------------------------------------------------------------------


class SubagentTaskRegistry:
    """进程级子代理任务注册表。

    线程安全（读写锁），持有后台任务引用防止 asyncio 任务被垃圾回收，
    前台/后台子代理一律注册，状态流转全程可查。
    """

    def __init__(self) -> None:
        self._tasks: dict[str, SubagentTask] = {}
        self._lock = threading.Lock()

    # -- 生命周期 --

    def register(
        self,
        agent_id: str,
        ctx: SubagentContext,
        *,
        agent_type: str = "general-purpose",
        description: str = "",
        mode: str = MODE_FOREGROUND,
        session_id: str | None = None,
        parent_session_id: str | None = None,
    ) -> SubagentTask:
        """注册一个子代理任务，初始状态 running。"""
        task = SubagentTask(
            agent_id=agent_id,
            agent_type=agent_type,
            description=description,
            session_id=session_id or agent_id,
            parent_session_id=parent_session_id,
            status=STATUS_RUNNING,
            mode=mode,
            ctx=ctx,
        )
        with self._lock:
            self._tasks[agent_id] = task
        logger.info(
            "子代理注册 %s (type=%s, mode=%s)", agent_id, agent_type, mode
        )
        return task

    def mark_status(self, agent_id: str, status: str, error: str | None = None) -> None:
        """更新任务状态。终态需附 usage 的场景由 set_result 处理。"""
        with self._lock:
            task = self._tasks.get(agent_id)
            if task is None:
                return
            task.status = status
            task.updated_at = time.time()
            if error is not None:
                task.error = error
        logger.info("子代理 %s 状态 -> %s", agent_id, status)

    def set_result(
        self,
        agent_id: str,
        *,
        status: str,
        final_text: str | None = None,
        usage: dict[str, int] | None = None,
        output_file: str | None = None,
        error: str | None = None,
    ) -> None:
        """写入最终结果：状态、最终文本、usage、落盘路径。"""
        with self._lock:
            task = self._tasks.get(agent_id)
            if task is None:
                return
            task.status = status
            task.updated_at = time.time()
            if final_text is not None:
                task.final_text = final_text
            if usage:
                task.usage.update(usage)
            if output_file:
                task.output_file = output_file
            if error is not None:
                task.error = error

    def attach_task(self, agent_id: str, async_task: asyncio.Task) -> None:
        """让注册表持有后台 asyncio 任务引用，防止引用丢失。"""
        with self._lock:
            task = self._tasks.get(agent_id)
            if task is not None:
                task.task = async_task

    def remove(self, agent_id: str) -> None:
        """移除任务记录（一般保留历史供查询，仅显式清理时调用）。"""
        with self._lock:
            self._tasks.pop(agent_id, None)

    # -- 查询 --

    def get(self, agent_id: str) -> SubagentTask | None:
        """获取任务记录。"""
        with self._lock:
            return self._tasks.get(agent_id)

    def get_status(self, agent_id: str) -> str | None:
        """获取任务状态。None 表示不在注册表中。"""
        task = self.get(agent_id)
        return task.status if task else None

    def get_ctx(self, agent_id: str) -> SubagentContext | None:
        """获取子代理执行上下文（运行期）。"""
        task = self.get(agent_id)
        return task.ctx if task else None

    def list_tasks(
        self,
        session_id: str | None = None,
        include_terminal: bool = True,
    ) -> list[SubagentTask]:
        """列出任务记录。

        Args:
            session_id: 按父会话过滤（None 列全部）
            include_terminal: 是否包含已结束的任务
        """
        with self._lock:
            tasks = list(self._tasks.values())
        if session_id is not None:
            tasks = [t for t in tasks if t.parent_session_id == session_id]
        if not include_terminal:
            tasks = [t for t in tasks if t.status not in TERMINAL_STATUSES]
        return sorted(tasks, key=lambda t: t.created_at)

    def running_count(self, session_id: str | None = None) -> int:
        """运行中任务数。session_id 非 None 时按父会话统计。"""
        return len(self.list_tasks(session_id=session_id, include_terminal=False))

    # -- 消息投递 --

    def queue_pending_message(self, agent_id: str, message: str) -> bool:
        """向运行中的子代理消息队列追加消息。

        Returns:
            True 成功入队；False 任务不存在或不在运行中
        """
        with self._lock:
            task = self._tasks.get(agent_id)
            if task is None or task.status != STATUS_RUNNING or task.ctx is None:
                return False
            task.ctx.pending_messages.append(message)
            return True


# ---------------------------------------------------------------------------
# 进程级单例
# ---------------------------------------------------------------------------

_subagent_registry = SubagentTaskRegistry()


def get_subagent_registry() -> SubagentTaskRegistry:
    """获取进程级子代理任务注册表。"""
    return _subagent_registry
