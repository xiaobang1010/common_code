"""Server 全局状态，由 __main__.py 启动时设置，路由模块读取。"""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

# 由 __main__.py 启动时设置
app_state: Any = None
engine: Any = None
permission_bridge: Any = None
# AskUserQuestion 提问桥，把模型提问转成 SSE 事件推给前端
question_bridge: Any = None
session_store: Any = None
# 当前对话任务，用于 abort 接口取消（旧单值，保留给兼容路径；任务管理用 running_runs）
current_task: Any = None
# 当前运行任务所属的会话 id（与 current_task 同步设置/清空，
# 供列表 API 透出"哪个任务在运行"，不落库）
current_session_id: Any = None
# 引擎当前装载的会话 id：无运行任务时也能判断引擎消息列表归属哪个会话
# （切换会话后设置、删除当前会话后清空），供删除/重置时精确判断
engine_session_id: Any = None


# ---------------------------------------------------------------------------
# 后台任务模型：任务与 SSE 连接解耦，每任务独立引擎与消息缓冲
# ---------------------------------------------------------------------------

# 任务工作区上下文变量：任务启动时设置自己所属的工作区，
# 工具沙箱 / Bash cwd / 记忆归属 / 系统提示词工作区信息读取时优先取它，
# 未设置（非任务上下文）回退全局 project_root()。
# asyncio.Task 创建时拷贝当前 context，任务内所有读取天然隔离。
workspace_var: ContextVar[str | None] = ContextVar("workspace_var", default=None)


@dataclass
class RunContext:
    """一次对话任务的运行上下文。

    每任务独立引擎（initial_messages 为 DB 会话消息前缀快照），
    绑定启动时的会话；SSE 生成器只是订阅者，断开不取消任务。
    """

    # 任务绑定的会话 id（收尾保存目标）
    session_id: str
    # 任务专属引擎实例（独立消息缓冲）
    engine: Any
    # 任务协程
    task: Any = None
    # 任务启动时间戳（秒）
    started_at: float = 0.0
    # 每任务独立的收尾事件：任务 finally 置位；abort/删除等待它
    finished: asyncio.Event = field(default_factory=asyncio.Event)
    # 任务级中断事件：/api/abort 置位后传导给引擎与前台子代理，触发优雅退出
    abort_event: asyncio.Event = field(default_factory=asyncio.Event)
    # SSE 订阅者队列集合：生成器注册/注销；无订阅者时任务事件丢弃
    subscribers: set = field(default_factory=set)


# 任务注册表：session_id -> RunContext。同一会话同时只允许一个运行任务。
# 供 abort/删除等待收尾、列表 API 透出运行态、/api/state 返回实时消息。
running_runs: dict[str, RunContext] = {}

# 等待任务收尾的超时秒数（abort/删除共用），超时后放弃操作不硬切
stream_finalize_timeout: float = 10.0
