"""子代理生命周期引擎 — 统一派生入口。

把原先散在 agent_tool / background 的派生逻辑收敛为单一入口，承载
执行底座的全部生命周期能力：

- 统一派生：构建上下文 → 应用预算默认 → 子会话绑定 → 驱动任务 → 前台/后台分流
- 前台自动转后台：前台运行超过阈值提升为后台，立即释放主轮次；
  提升后的代理换独立中断事件，与父会话中止解绑（detach）
- 活性看门狗：超过配置时长无任何活动即中止，终态 stopped
- 中止即时链：停止/父中止直接取消驱动任务，CancelledError 沿
  run_agent 的 async for 传播到模型流，秒级生效；轮次边界检查保留为兜底
- 结果处理：截断落盘、注册表终态、子会话 agent_meta 同步、父会话通知

单一写者原则：注册表终态与子会话元数据只在本模块的状态流转点写入。
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from tools.protocol import Tool, ToolUseContext
from tools.subagent import registry as _registry_mod
from tools.subagent.context import SubagentContext, create_subagent_context
from tools.subagent.registry import (
    MODE_BACKGROUND,
    MODE_FOREGROUND,
    STATUS_ABORTED,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_STOPPED,
    SubagentTask,
)
from tools.subagent.types import AgentDefinition

logger = logging.getLogger(__name__)

# 后台结果截断阈值（与前台路径一致）
MAX_RESULT_SIZE_CHARS = 100_000


# ---------------------------------------------------------------------------
# 派生请求与结果
# ---------------------------------------------------------------------------


@dataclass
class SpawnRequest:
    """派生请求（由 Agent 工具构建）。

    Attributes:
        agent_def: 代理类型定义（解析器已命中）
        prompt: 任务指令
        description: 3-5 词任务描述
        parent_context: 父代理工具执行上下文（携带父会话标识与中断事件）
        run_in_background: 是否直接后台运行
    """

    agent_def: AgentDefinition
    prompt: str
    description: str
    parent_context: ToolUseContext | None
    run_in_background: bool = False


@dataclass
class SubagentOutcome:
    """驱动任务的终态结果。"""

    status: str
    final_text: str = ""
    output_file: str | None = None
    error: str | None = None


@dataclass
class SpawnResult:
    """spawn_subagent 返回。

    Attributes:
        kind: "completed"（前台跑完）或 "async_launched"（后台/被提升）
        agent_id: 子代理标识
        outcome: 前台完成时的终态结果（async 时为 None）
        task: 注册表任务记录
    """

    kind: str
    agent_id: str
    outcome: SubagentOutcome | None = None
    task: SubagentTask | None = None


# ---------------------------------------------------------------------------
# 预算与配置
# ---------------------------------------------------------------------------


def _get_subagents_config():
    """读取全局配置 subagents 段（未就绪返回 None）。"""
    try:
        from startup.config import get_global_config

        return get_global_config().subagents
    except Exception:
        return None


def _apply_budget_defaults(ctx: SubagentContext, agent_def: AgentDefinition) -> None:
    """预算双护栏：轮次上限与 token 预算，profile 未指定时应用全局默认。"""
    cfg = _get_subagents_config()
    if ctx.max_turns is None:
        default_turns = cfg.max_turns_default if cfg is not None else 50
        ctx.max_turns = default_turns if default_turns > 0 else None
    if ctx.token_budget is None:
        if agent_def.token_budget is not None:
            ctx.token_budget = agent_def.token_budget
        else:
            default_budget = cfg.token_budget_default if cfg is not None else 0
            ctx.token_budget = default_budget if default_budget > 0 else None


# ---------------------------------------------------------------------------
# 活性看门狗
# ---------------------------------------------------------------------------


class ActivityWatchdog:
    """活性看门狗：超过配置时长无活动即中止子代理。

    runner 每条消息上报活动（report）；看门狗按超时周期检查，
    超时触发回调（由生命周期设置为「记原因 + 取消驱动任务」）。
    """

    def __init__(self, timeout_ms: int, on_timeout) -> None:
        self._timeout_s = max(timeout_ms, 1) / 1000.0
        self._on_timeout = on_timeout
        self._last_activity = time.monotonic()
        self._task: asyncio.Task | None = None

    def report(self) -> None:
        """活动上报（每条消息调用）。"""
        self._last_activity = time.monotonic()

    def start(self) -> None:
        self._task = asyncio.get_event_loop().create_task(self._loop())

    def stop(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self._task = None

    async def _loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._timeout_s)
                idle = time.monotonic() - self._last_activity
                if idle >= self._timeout_s:
                    self._on_timeout()
                    return
        except asyncio.CancelledError:
            pass  # 正常停止


# ---------------------------------------------------------------------------
# 驱动任务：消费 + 终态处理
# ---------------------------------------------------------------------------


async def _consume(ctx: SubagentContext, tools: list[Tool], system_prompt: str) -> str:
    """消费 run_agent 消息流，返回最终 assistant 文本。"""
    from tools.subagent.runner import run_agent
    from tools.subagent.transcript import append_task_output

    final_text = ""
    async for message in run_agent(ctx=ctx, tools=tools, system_prompt=system_prompt):
        if isinstance(message, dict) and message.get("role") == "assistant":
            content = message.get("content", "")
            if content:
                final_text = content
                # 增量输出文件：后台/提升代理的阶段性输出来源
                if ctx.is_async:
                    append_task_output(ctx.agent_id, content)
    return final_text


def _truncate_result(agent_id: str, final_text: str) -> tuple[str, str | None]:
    """超阈值截断并落盘，返回 (截断后文本, 落盘路径)。"""
    if len(final_text) <= MAX_RESULT_SIZE_CHARS:
        return final_text, None
    from tools.subagent.transcript import save_full_result

    result_path = save_full_result(agent_id, final_text)
    truncated = (
        final_text[:MAX_RESULT_SIZE_CHARS]
        + f"\n\n[Result truncated. Full output saved to: {result_path}]"
    )
    return truncated, result_path


def _sync_terminal_meta(ctx: SubagentContext, status: str, outcome: SubagentOutcome) -> None:
    """终态同步子会话 agent_meta（失败不影响主流程）。"""
    try:
        from tools.subagent.session_binding import update_child_meta

        update_child_meta(
            ctx.agent_id,
            status=status,
            usage=dict(ctx.usage),
            output_file=outcome.output_file,
        )
    except Exception as e:
        logger.warning("子会话终态同步失败: %s", e)


def _notify_parent(task: SubagentTask, status: str) -> None:
    """向父会话投递终态通知（格式见 notify 模块）。"""
    try:
        from tools.subagent import notify

        if not task.parent_session_id:
            return
        notify.push_notification(
            task.parent_session_id,
            notify.format_completion_notification(task, status),
        )
    except Exception as e:
        logger.warning("父会话通知投递失败: %s", e)


def _log_lifecycle_event(event: str, task: SubagentTask, error: str = "") -> None:
    """结构化生命周期事件日志（spawned/promoted/completed/failed/stopped）。"""
    logger.info(
        "子代理生命周期事件 %s (agent_id=%s, type=%s, mode=%s, promoted=%s, error=%s)",
        event,
        task.agent_id,
        task.agent_type,
        task.mode,
        task.promoted,
        error,
    )


async def _run_driver(
    task: SubagentTask,
    ctx: SubagentContext,
    tools: list[Tool],
    system_prompt: str,
) -> SubagentOutcome:
    """驱动任务体：消费子代理循环并统一处理终态。

    前台与后台共用；终态写入注册表、同步子会话元数据、投递父会话通知。
    CancelledError 不外抛——显式停止（看门狗/主动停止）记 stopped，
    其余取消（父会话中止传导）记 aborted。
    """
    registry = _registry_mod.get_subagent_registry()
    final_text = ""
    try:
        final_text = await _consume(ctx, tools, system_prompt)
    except asyncio.CancelledError:
        status = STATUS_STOPPED if ctx.stop_reason else STATUS_ABORTED
        error = ctx.stop_reason or "parent session aborted"
        outcome = SubagentOutcome(status=status, error=error)
        registry.set_result(
            ctx.agent_id, status=status, error=error, usage=dict(ctx.usage)
        )
        _sync_terminal_meta(ctx, status, outcome)
        _notify_parent(task, status)
        _log_lifecycle_event(status, task, error=error)
        return outcome
    except Exception as e:
        logger.exception("子代理 %s 运行异常: %s", ctx.agent_id, e)
        outcome = SubagentOutcome(status=STATUS_FAILED, error=str(e))
        registry.set_result(
            ctx.agent_id, status=STATUS_FAILED, error=str(e), usage=dict(ctx.usage)
        )
        _sync_terminal_meta(ctx, STATUS_FAILED, outcome)
        _notify_parent(task, STATUS_FAILED)
        _log_lifecycle_event(STATUS_FAILED, task, error=str(e))
        return outcome

    # 正常结束：父中断事件已置位按 aborted 记录（前台共享父事件的语义保留）
    was_aborted = ctx.abort_event is not None and ctx.abort_event.is_set()
    status = STATUS_ABORTED if was_aborted else STATUS_COMPLETED
    final_text, output_file = _truncate_result(ctx.agent_id, final_text)
    outcome = SubagentOutcome(
        status=status,
        final_text=final_text,
        output_file=output_file,
        error="parent session aborted" if was_aborted else None,
    )
    registry.set_result(
        ctx.agent_id,
        status=status,
        final_text=final_text,
        output_file=output_file,
        usage=dict(ctx.usage),
        error=outcome.error,
    )
    _sync_terminal_meta(ctx, status, outcome)
    _notify_parent(task, status)
    _log_lifecycle_event(status, task)
    return outcome


# ---------------------------------------------------------------------------
# 后台启动
# ---------------------------------------------------------------------------


def _launch_background_driver(
    task: SubagentTask,
    ctx: SubagentContext,
    tools: list[Tool],
    system_prompt: str,
) -> None:
    """为注册表任务创建驱动任务并持有引用（防垃圾回收）。"""
    registry = _registry_mod.get_subagent_registry()
    driver = asyncio.get_event_loop().create_task(
        _run_driver(task, ctx, tools, system_prompt)
    )
    registry.attach_task(ctx.agent_id, driver)


async def _watch_parent_abort(
    parent_event: asyncio.Event, driver: asyncio.Task
) -> None:
    """父中止即时链：父会话中断置位后立即取消驱动任务（不等轮次边界）。

    CancelledError 沿 run_agent 的 async for 传播到模型流；
    轮次边界的 abort_event 检查保留为兜底。
    """
    try:
        await parent_event.wait()
        if not driver.done():
            driver.cancel()
    except asyncio.CancelledError:
        pass  # 驱动先结束，监听随之撤销


# ---------------------------------------------------------------------------
# 统一派生入口
# ---------------------------------------------------------------------------


async def spawn_subagent(request: SpawnRequest) -> SpawnResult:
    """统一派生入口：前台/后台分流，前台支持自动转后台。

    前台路径三路竞态：完成 / 提升定时器 / （父中止经取消链传导）。
    提升时驱动任务换独立中断事件继续跑、从父中止监听解绑、
    立即返回 async_launched 形态结果，主轮次不再阻塞。
    """
    from query.services.api.client import get_default_model
    from tools import get_tools
    from tools.subagent.context import build_subagent_system_prompt
    from tools.subagent.session_binding import ensure_child_session
    from tools.subagent.tools import resolve_agent_tools

    agent_def = request.agent_def
    parent_context = request.parent_context

    # 父会话中断事件（/api/abort 经 ToolUseContext.abort_controller 下发）
    parent_abort_event = (
        parent_context.abort_controller
        if parent_context is not None
        and isinstance(parent_context.abort_controller, asyncio.Event)
        else None
    )
    parent_session_id = (
        parent_context.session_id if parent_context is not None else ""
    ) or None

    # 隔离上下文 + 预算默认
    ctx = create_subagent_context(
        parent_context=parent_context,
        agent_def=agent_def,
        main_loop_model=get_default_model(),
        is_async=request.run_in_background,
        prompt=request.prompt,
        parent_abort_event=parent_abort_event,
    )
    _apply_budget_defaults(ctx, agent_def)

    # 系统提示词与工具池
    system_prompt = build_subagent_system_prompt(agent_def)
    worker_tools = resolve_agent_tools(agent_def, get_tools())

    # 子会话绑定（upsert；失败降级为无子会话模式，不阻断派生）
    try:
        from server.paths import effective_root

        workspace_path = effective_root()
    except Exception:
        workspace_path = ""
    try:
        ctx.child_session_id = (
            ensure_child_session(
                ctx.agent_id,
                parent_session_id=parent_session_id,
                workspace_path=workspace_path,
                title=request.description,
                agent_type=agent_def.agent_type,
                mode=MODE_BACKGROUND if request.run_in_background else MODE_FOREGROUND,
            )
            or ""
        )
    except Exception as e:
        logger.warning("子会话绑定失败（降级为无子会话模式）: %s", e)
        ctx.child_session_id = ""

    # 活性看门狗（0=关闭）
    cfg = _get_subagents_config()
    watchdog: ActivityWatchdog | None = None
    watchdog_timeout = cfg.inactivity_timeout_ms if cfg is not None else 0
    if watchdog_timeout > 0:
        def _on_watchdog_timeout() -> None:
            ctx.stop_reason = "inactivity timeout"
            task_rec = _registry_mod.get_subagent_registry().get(ctx.agent_id)
            if task_rec is not None and task_rec.task is not None:
                task_rec.task.cancel()

        watchdog = ActivityWatchdog(watchdog_timeout, _on_watchdog_timeout)
        ctx.on_activity = watchdog.report

    registry = _registry_mod.get_subagent_registry()

    # ---- 后台路径：注册 + 驱动任务，立即返回 ----
    if request.run_in_background:
        task = registry.register(
            ctx.agent_id,
            ctx,
            agent_type=agent_def.agent_type,
            description=request.description,
            mode=MODE_BACKGROUND,
            parent_session_id=parent_session_id,
            child_session_id=ctx.child_session_id,
        )
        _launch_background_driver(task, ctx, worker_tools, system_prompt)
        if watchdog is not None:
            watchdog.start()
            # 驱动结束（任何终态）随之停止看门狗，避免泄漏
            task.task.add_done_callback(lambda _t: watchdog.stop())
        _log_lifecycle_event("spawned", task)
        return SpawnResult(kind="async_launched", agent_id=ctx.agent_id, task=task)

    # ---- 前台路径：驱动任务 + 提升竞态 ----
    task = registry.register(
        ctx.agent_id,
        ctx,
        agent_type=agent_def.agent_type,
        description=request.description,
        mode=MODE_FOREGROUND,
        parent_session_id=parent_session_id,
        child_session_id=ctx.child_session_id,
    )
    driver = asyncio.get_event_loop().create_task(
        _run_driver(task, ctx, worker_tools, system_prompt)
    )
    registry.attach_task(ctx.agent_id, driver)
    if watchdog is not None:
        watchdog.start()
        # 驱动结束（任何终态，含提升后跑完）随之停止看门狗，避免泄漏
        driver.add_done_callback(lambda _t: watchdog.stop())
    _log_lifecycle_event("spawned", task)

    # 父中止即时链：父会话中断置位后立即取消驱动任务
    parent_watcher: asyncio.Task | None = None
    if parent_abort_event is not None:
        parent_watcher = asyncio.get_event_loop().create_task(
            _watch_parent_abort(parent_abort_event, driver)
        )

    auto_background_ms = cfg.auto_background_ms if cfg is not None else 0
    promote_timer: asyncio.Task | None = None
    if auto_background_ms > 0:
        promote_timer = asyncio.get_event_loop().create_task(
            asyncio.sleep(auto_background_ms / 1000.0)
        )

    try:
        try:
            if promote_timer is None:
                outcome = await driver
                return SpawnResult(
                    kind="completed", agent_id=ctx.agent_id, outcome=outcome, task=task
                )

            done, _pending = await asyncio.wait(
                {driver, promote_timer}, return_when=asyncio.FIRST_COMPLETED
            )
            if driver in done:
                return SpawnResult(
                    kind="completed",
                    agent_id=ctx.agent_id,
                    outcome=driver.result(),
                    task=task,
                )

            # 提升定时器先到：转后台，立即释放主轮次
            promote_timer.cancel()
            registry.mark_promoted(ctx.agent_id)
            # 与父中止解绑：换独立中断事件并撤销父中止监听，
            # 父会话后续 abort 不影响已提升代理
            ctx.abort_event = asyncio.Event()
            ctx.is_async = True
            if parent_watcher is not None:
                parent_watcher.cancel()
                parent_watcher = None
            _log_lifecycle_event("promoted", task)
            _notify_promoted(task)
            return SpawnResult(kind="async_launched", agent_id=ctx.agent_id, task=task)
        except asyncio.CancelledError:
            # 父所在任务被强杀：把取消传导给驱动任务（驱动内记 aborted），
            # 再重新抛出保证取消语义
            if not driver.done():
                driver.cancel()
            raise
    finally:
        if promote_timer is not None and not promote_timer.done():
            promote_timer.cancel()
        if parent_watcher is not None and not parent_watcher.done():
            parent_watcher.cancel()
        # 看门狗随驱动任务存续；驱动已完成时停止
        if driver.done() and watchdog is not None:
            watchdog.stop()


def _notify_promoted(task: SubagentTask) -> None:
    """提升通知：告知主代理任务已转后台、建议继续其他工作。"""
    try:
        from tools.subagent import notify

        if not task.parent_session_id:
            return
        notify.push_notification(
            task.parent_session_id,
            notify.format_promoted_notification(task),
        )
    except Exception as e:
        logger.warning("提升通知投递失败: %s", e)


# ---------------------------------------------------------------------------
# 统一停止（中止即时链）
# ---------------------------------------------------------------------------


def stop_subagent(agent_id: str, reason: str = "stopped by request") -> str:
    """统一停止入口：取消驱动任务，秒级生效。

    Returns:
        停止后的即时状态描述（"stopping" / "stopped" / "already_finished" / "not_found"）
    """
    from tools.subagent.registry import TERMINAL_STATUSES

    registry = _registry_mod.get_subagent_registry()
    task = registry.get(agent_id)
    if task is None:
        return "not_found"
    if task.status in TERMINAL_STATUSES:
        return "already_finished"

    driver = task.task
    if driver is not None and not driver.done():
        # 记录显式停止原因后取消驱动任务：
        # CancelledError 沿 run_agent 的 async for 传播到模型流
        if task.ctx is not None and not task.ctx.stop_reason:
            task.ctx.stop_reason = reason
        driver.cancel()
        return "stopping"

    # 兜底：无驱动句柄时置位中断事件（轮次边界优雅退出）
    if task.ctx is not None and task.ctx.abort_event is not None:
        task.ctx.abort_event.set()
        return "stopping"
    from tools.subagent.registry import STATUS_STOPPED

    registry.mark_status(agent_id, STATUS_STOPPED, error=reason)
    return "stopped"


# ---------------------------------------------------------------------------
# 供 background.py 薄转发的后台启动（保留旧入口兼容）
# ---------------------------------------------------------------------------


def launch_background_subagent(
    ctx: SubagentContext,
    tools: list[Tool],
    system_prompt: str,
    *,
    description: str = "",
    parent_session_id: str | None = None,
) -> SubagentTask:
    """启动后台子代理（旧入口，保留签名兼容）。

    新代码请直接使用 spawn_subagent。
    """
    registry = _registry_mod.get_subagent_registry()
    task = registry.register(
        ctx.agent_id,
        ctx,
        agent_type=ctx.agent_def.agent_type,
        description=description,
        mode=MODE_BACKGROUND,
        parent_session_id=parent_session_id,
        child_session_id=getattr(ctx, "child_session_id", ""),
    )
    _launch_background_driver(task, ctx, tools, system_prompt)
    return task
