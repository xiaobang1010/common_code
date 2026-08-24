"""子代理通知队列 - 后台任务完成通知的双通道之一（非活跃持久化通道）。

后台子代理完成时向父会话投递通知：
- 父会话活跃（query_loop 运行中）：loop 在每轮工具收尾时 drain 注入对话；
- 父会话不活跃：通知留在队列，父会话下次运行时 drain，不丢失。
"""

from __future__ import annotations

import threading

# parent_session_id -> 待投递通知消息列表（OpenAI 格式 dict）
_pending_notifications: dict[str, list[dict]] = {}
_lock = threading.Lock()


def push_notification(session_id: str, message: dict) -> None:
    """向父会话通知队列追加一条消息。

    Args:
        session_id: 父会话标识（引擎 session_id / 聊天会话 id）
        message: OpenAI 格式消息 dict（如 {"role": "user", "content": ...}）
    """
    if not session_id:
        return
    with _lock:
        _pending_notifications.setdefault(session_id, []).append(message)


def drain_notifications(session_id: str) -> list[dict]:
    """取出并清空该会话的全部待投递通知。"""
    with _lock:
        return _pending_notifications.pop(session_id, [])


def pending_count(session_id: str) -> int:
    """该会话待投递通知数（观测用）。"""
    with _lock:
        return len(_pending_notifications.get(session_id, []))
