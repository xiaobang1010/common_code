"""后台唤起测试 — 守卫、通知合并、指针不劫持、收尾窗口补偿。

直接驱动 chat 路由的 _start_run / _wake_parent（不经过 HTTP），用 FakeEngine
模拟引擎（沿用 test_chat_session_binding 的装配模式），验证：
- 空闲父会话被唤起：通知作为用户消息建轮次，队列取走不丢失
- 父会话占用时不唤起（活跃通道负责）；开关关闭时不唤起；去重只建一轮
- 唤起不改写查看会话指针
- 运行收尾窗口内到达的通知在收尾后补偿唤起
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import server.state
from server.routers.chat import routes as chat_routes
from server.permission_bridge import PermissionBridge
from server.question_bridge import QuestionBridge
from session.store import SessionStore
from tools.subagent import notify

from tests.test_chat_session_binding import FakeEngine, FakeAppState


@pytest.fixture
def env(workspace, monkeypatch):
    """装配 server.state：FakeEngine + 真实 SessionStore + 真实桥（同 binding 测试）。"""
    store = SessionStore(db_path=workspace / "sessions.db")
    engine = FakeEngine()
    monkeypatch.setattr(server.state, "engine", engine)
    monkeypatch.setattr(server.state, "running_runs", {})
    monkeypatch.setattr(server.state, "session_store", store)
    monkeypatch.setattr(server.state, "permission_bridge", PermissionBridge())
    monkeypatch.setattr(server.state, "question_bridge", QuestionBridge())
    monkeypatch.setattr(server.state, "app_state", FakeAppState())
    monkeypatch.setattr(server.state, "engine_session_id", None)
    from query.engine import QueryEngineConfig

    monkeypatch.setattr(chat_routes, "build_engine_config", lambda **kw: QueryEngineConfig())
    monkeypatch.setattr(chat_routes, "QueryEngine", lambda config, initial_messages=None, session_id="": engine)
    chat_routes._waking_sessions.clear()
    notify.register_wakeup_hook(None)
    yield engine, store
    notify.register_wakeup_hook(None)
    chat_routes._waking_sessions.clear()


async def _wait_until(predicate, timeout_s: float = 3.0) -> bool:
    """轮询等待条件成立（唤起/收尾都是后台任务，用短间隔轮询观测）。"""
    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.02)
    return predicate()


@pytest.mark.asyncio
async def test_wake_creates_run_and_persists_notification(workspace, env):
    """空闲父会话被唤起：通知作为用户消息建轮次，队列取走不丢失。"""
    engine, store = env
    store.add_workspace(str(workspace))
    sid = store.create_session(str(workspace), title="工作流会话").id

    notify.push_notification(sid, {"role": "user", "content": "<task-notification>任务完成</task-notification>"})
    chat_routes._wake_parent(sid)
    # 唤起任务把通知驱动为一轮运行：会话出现在运行注册表
    assert await _wait_until(lambda: sid in server.state.running_runs)

    engine.release.set()
    assert await _wait_until(lambda: sid not in server.state.running_runs)

    # 通知正文进入会话历史（作为用户消息持久化）
    saved = store.get_session(sid).messages
    assert any(m.get("role") == "user" and "task-notification" in str(m.get("content", "")) for m in saved)
    assert notify.pending_count(sid) == 0


@pytest.mark.asyncio
async def test_wake_skipped_when_session_busy(workspace, env):
    """父会话在运行中时不唤起（活跃通道负责），通知留队列。"""
    engine, store = env
    store.add_workspace(str(workspace))
    sid = store.create_session(str(workspace), title="占用中").id

    run, err = chat_routes._start_run(sid, "进行中的一轮")
    assert err is None

    notify.push_notification(sid, {"role": "user", "content": "n"})
    chat_routes._wake_parent(sid)
    await asyncio.sleep(0.05)

    # 没有额外的唤起任务产生：去重集为空、通知仍在队列
    assert chat_routes._waking_sessions == set()
    assert notify.pending_count(sid) == 1
    engine.release.set()


@pytest.mark.asyncio
async def test_wake_skipped_when_disabled(workspace, env, monkeypatch):
    """开关关闭时不唤起，通知留队列（回退现状语义）。"""
    engine, store = env
    store.add_workspace(str(workspace))
    sid = store.create_session(str(workspace), title="关开关").id

    from startup.config import get_global_config  # noqa: F401  确认模块可导入，实际打桩在下方

    def fake_config():
        return SimpleNamespace(subagents=SimpleNamespace(auto_resume_parent=False))

    monkeypatch.setattr("startup.config.get_global_config", fake_config)

    notify.push_notification(sid, {"role": "user", "content": "n"})
    chat_routes._wake_parent(sid)
    await asyncio.sleep(0.05)

    assert sid not in server.state.running_runs
    assert notify.pending_count(sid) == 1


@pytest.mark.asyncio
async def test_wake_dedup_only_one_run(workspace, env, monkeypatch):
    """通知风暴去重：唤起中重复触发只建一轮。"""
    engine, store = env
    store.add_workspace(str(workspace))
    sid = store.create_session(str(workspace), title="风暴").id

    calls: list[str] = []

    def fake_start_run(session_id, prompt, **kw):
        # 同步假核心：签名与真实 _start_run 一致（返回二元组，非协程）
        calls.append(session_id)
        return SimpleNamespace(finished=asyncio.Event(), subscribers=set(), task=None), None

    monkeypatch.setattr(chat_routes, "_start_run", fake_start_run)

    notify.push_notification(sid, {"role": "user", "content": "n1"})
    chat_routes._wake_parent(sid)
    chat_routes._wake_parent(sid)
    chat_routes._wake_parent(sid)
    await asyncio.sleep(0.1)

    assert calls == [sid]
    assert chat_routes._waking_sessions == set()


@pytest.mark.asyncio
async def test_wake_does_not_take_view_pointer(workspace, env):
    """唤起不改写查看会话指针：用户正查看其他会话不受污染。"""
    engine, store = env
    store.add_workspace(str(workspace))
    sid = store.create_session(str(workspace), title="被唤起会话").id
    server.state.engine_session_id = "sess_viewing_other"

    notify.push_notification(sid, {"role": "user", "content": "n"})
    chat_routes._wake_parent(sid)
    assert await _wait_until(lambda: sid in server.state.running_runs)

    engine.release.set()
    assert await _wait_until(lambda: sid not in server.state.running_runs)
    assert server.state.engine_session_id == "sess_viewing_other"


@pytest.mark.asyncio
async def test_finalize_compensates_notification_during_run(workspace, env):
    """收尾窗口补偿：运行期间到达的通知，收尾移出注册表后补一次唤起。"""
    engine, store = env
    store.add_workspace(str(workspace))
    sid = store.create_session(str(workspace), title="补偿场景").id

    run, err = chat_routes._start_run(sid, "第一轮")
    assert err is None
    await asyncio.sleep(0.05)

    # 运行收尾窗口内到达的通知：此刻会话仍在注册表，钩子守卫会跳过
    notify.push_notification(sid, {"role": "user", "content": "<task-notification>后台完成</task-notification>"})
    engine.release.set()

    # 第一轮收尾 → 补偿唤起第二轮；第二轮因引擎已放行瞬间完成并出注册表，
    # 观察注册表不可靠——用持久化结果证明补偿发生（通知以用户消息入库）
    await asyncio.sleep(0.3)
    saved = store.get_session(sid).messages
    assert any(m.get("role") == "user" and "task-notification" in str(m.get("content", "")) for m in saved)
    assert notify.pending_count(sid) == 0
