"""子代理通知队列 - 后台任务完成通知的多通道入口。

后台子代理完成时向父会话投递通知：
- 父会话活跃（query_loop 运行中）：loop 在每轮工具收尾时 drain 注入对话；
- 父会话不活跃：通知留在队列。server 侧注册的唤起钩子据此自动创建
  父会话运行任务（auto-resume），无钩子环境（如单测）保持纯队列语义。

通知为统一的「任务通知」格式（taskType=local_agent），覆盖
完成/失败/停止/预算停止/提升五类事件。
"""

import logging
import threading
from typing import Callable

logger = logging.getLogger(__name__)

# parent_session_id -> 待投递通知消息列表（OpenAI 格式 dict）
_pending_notifications: dict[str, list[dict]] = {}
_lock = threading.Lock()

# 唤起回调：server 启动时经 register_wakeup_hook 注入，tools 层不反向依赖 server
_wakeup_hook: Callable[[str], None] | None = None


def register_wakeup_hook(hook: Callable[[str], None]) -> None:
    """注册唤起回调（入参为父会话 id），重复注册以最后一次为准。"""
    global _wakeup_hook
    _wakeup_hook = hook


def push_notification(session_id: str, message: dict) -> None:
    """向父会话通知队列追加一条消息，并触发唤起钩子。

    Args:
        session_id: 父会话标识（引擎 session_id / 聊天会话 id）
        message: OpenAI 格式消息 dict（如 {"role": "user", "content": ...}）
    """
    if not session_id:
        return
    with _lock:
        _pending_notifications.setdefault(session_id, []).append(message)
    # 钩子在锁外触发：回调内部可能再操作队列；异常只记日志，不影响入队
    hook = _wakeup_hook
    if hook is not None:
        try:
            hook(session_id)
        except Exception as e:
            logger.warning("唤起钩子执行失败 (session=%s): %s", session_id, e)


def drain_notifications(session_id: str) -> list[dict]:
    """取出并清空该会话的全部待投递通知。"""
    with _lock:
        return _pending_notifications.pop(session_id, [])


def pending_count(session_id: str) -> int:
    """该会话待投递通知数（观测用）。"""
    with _lock:
        return len(_pending_notifications.get(session_id, []))


# ---------------------------------------------------------------------------
# 统一任务通知格式
# ---------------------------------------------------------------------------


def format_completion_notification(task, status: str) -> dict:
    """构造终态任务通知（完成/失败/停止/预算停止共用）。

    统一字段：任务类型、状态、耗时、工具调用数、tokens、结果预览、
    后续操控指引（GetSubagentOutput / SendMessage）。
    """
    duration_ms = int((task.updated_at - task.created_at) * 1000)
    usage = task.usage or {}
    preview = (task.final_text or "")[:200]
    lines = [
        "<task-notification>",
        "taskType: local_agent",
        f"agent_id: {task.agent_id} (type={task.agent_type})",
        f"status: {status}",
        f"耗时: {duration_ms}ms, 工具调用: {usage.get('tool_uses', 0)} 次, "
        f"tokens: {usage.get('total_tokens', 0)}",
    ]
    if getattr(task, "promoted", False):
        lines.append("mode: background (promoted from foreground)")
    if task.error:
        lines.append(f"原因: {task.error}")
    if preview:
        lines.append(f"结果预览: {preview}")
    lines.append(
        f"可用 GetSubagentOutput 按 agent_id={task.agent_id} 查看完整结果，"
        f"或 SendMessage 续聊。"
    )
    lines.append("</task-notification>")
    return {"role": "user", "content": "\n".join(lines)}


def format_promoted_notification(task) -> dict:
    """构造「已转后台」通知：告知主代理任务提升为后台，建议继续其他工作。"""
    lines = [
        "<task-notification>",
        "taskType: local_agent",
        f"agent_id: {task.agent_id} (type={task.agent_type})",
        "status: promoted",
        f"前台任务已自动转为后台运行（agent_id={task.agent_id}）。"
        "请继续处理其他工作，不要重复该任务正在做的事；完成后会收到通知。",
        "</task-notification>",
    ]
    return {"role": "user", "content": "\n".join(lines)}
