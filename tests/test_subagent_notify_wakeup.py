"""notify 唤起钩子测试 — 注册、触发与异常安全。"""

from __future__ import annotations

import pytest

from tools.subagent import notify


@pytest.fixture(autouse=True)
def _reset_hook():
    """每个用例前后清掉钩子，避免污染其他测试。"""
    notify.register_wakeup_hook(None)
    yield
    notify.register_wakeup_hook(None)


def test_push_notification_invokes_hook():
    """入队后钩子收到父会话 id。"""
    seen: list[str] = []
    notify.register_wakeup_hook(seen.append)

    notify.push_notification("sess_hook1", {"role": "user", "content": "n1"})
    notify.push_notification("sess_hook1", {"role": "user", "content": "n2"})

    assert seen == ["sess_hook1", "sess_hook1"]
    assert notify.pending_count("sess_hook1") == 2


def test_hook_exception_does_not_break_enqueue():
    """钩子抛异常只被吞掉，通知照常入队（唤起失败不影响通知本身）。"""
    def boom(_sid: str) -> None:
        raise RuntimeError("唤醒失败")

    notify.register_wakeup_hook(boom)
    notify.push_notification("sess_hook2", {"role": "user", "content": "n"})

    assert notify.pending_count("sess_hook2") == 1


def test_no_hook_registered_is_pure_queue():
    """未注册钩子（如单测环境）保持纯队列语义。"""
    notify.push_notification("sess_hook3", {"role": "user", "content": "n"})
    assert notify.drain_notifications("sess_hook3") == [{"role": "user", "content": "n"}]
    assert notify.drain_notifications("sess_hook3") == []
