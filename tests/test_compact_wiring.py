"""compact 命令 HTTP 接线测试。

验证 /api/command 的 compact 分支注入引擎真实消息与压缩函数后
能真实执行压缩（原地改写消息列表），且其余命令（/clear）行为不变、
运行中任务优先取任务引擎消息。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import query.services.compact.auto_compact as auto_compact
import server.state
from server.routers.commands.routes import run_command
from startup.state.app_state import AppState, AppStateProvider


def _fake_compact(monkeypatch, kept: list[dict]) -> None:
    """把 compact_conversation 打桩为固定返回，避免真实 LLM 调用。"""

    async def fake(messages: list[dict], model: str) -> list[dict]:
        return kept

    monkeypatch.setattr(auto_compact, "compact_conversation", fake)


@pytest.fixture
def view_engine(monkeypatch):
    """伪造查看引擎与 app_state，返回引擎消息列表引用。"""
    msgs = [
        {"role": "user", "content": "U" * 200},
        {"role": "assistant", "content": "A" * 200},
        {"role": "user", "content": "最新问题"},
    ]
    monkeypatch.setattr(server.state, "engine", SimpleNamespace(mutable_messages=msgs))
    monkeypatch.setattr(server.state, "engine_session_id", None)
    monkeypatch.setattr(server.state, "running_runs", {})
    monkeypatch.setattr(
        server.state, "app_state", AppStateProvider(AppState(model="fake-model"))
    )
    return msgs


def test_compact_compacts_real_engine_messages(monkeypatch, view_engine):
    """非空会话：compact 作用于引擎真实消息，压缩后消息数下降并返回成功文案。"""
    _fake_compact(monkeypatch, [{"role": "user", "content": "[摘要]"}])

    result = asyncio.run(run_command({"command": "/compact"}))

    assert "compacted" in result["output"]
    assert len(view_engine) == 1
    assert view_engine[0]["content"] == "[摘要]"


def test_compact_empty_session_returns_hint(monkeypatch, view_engine):
    """空会话：返回原提示，引擎消息不动。"""
    view_engine.clear()
    _fake_compact(monkeypatch, [])

    result = asyncio.run(run_command({"command": "/compact"}))

    assert "No messages" in result["output"]
    assert view_engine == []


def test_compact_prefers_running_task_engine(monkeypatch, view_engine):
    """查看会话有运行中任务时，compact 作用于任务引擎的消息。"""
    run_msgs = [{"role": "user", "content": "任务消息"}]
    run = SimpleNamespace(
        engine=SimpleNamespace(mutable_messages=run_msgs),
        finished=asyncio.Event(),
    )
    monkeypatch.setattr(server.state, "engine_session_id", "s1")
    monkeypatch.setattr(server.state, "running_runs", {"s1": run})
    _fake_compact(monkeypatch, [{"role": "user", "content": "[摘要]"}])

    result = asyncio.run(run_command({"command": "/compact"}))

    assert "compacted" in result["output"]
    assert [m["content"] for m in run_msgs] == ["[摘要]"]
    # 查看引擎消息未被误动
    assert len(view_engine) == 3


def test_clear_does_not_touch_engine_messages(monkeypatch, view_engine):
    """/clear 维持原行为：不注入引擎消息，HTTP 侧仍为空操作。"""
    result = asyncio.run(run_command({"command": "/clear"}))

    assert "output" in result
    assert len(view_engine) == 3


class FakeStore:
    """记录 save_messages 调用的假会话存储。"""

    def __init__(self) -> None:
        self.saved: list[tuple[str, list]] = []

    def save_messages(self, session_id: str, messages: list) -> None:
        self.saved.append((session_id, list(messages)))


def test_compact_success_persists_to_store(monkeypatch, view_engine):
    """空闲查看引擎压缩成功：结果落库，防止下一轮从 DB 回灌未压缩历史。"""
    store = FakeStore()
    monkeypatch.setattr(server.state, "session_store", store)
    monkeypatch.setattr(server.state, "engine_session_id", "s0")
    _fake_compact(monkeypatch, [{"role": "user", "content": "[摘要]"}])

    result = asyncio.run(run_command({"command": "/compact"}))

    assert "compacted" in result["output"]
    assert store.saved == [("s0", [{"role": "user", "content": "[摘要]"}])]


def test_compact_running_task_skips_persist(monkeypatch, view_engine):
    """运行中任务压缩成功：不落库（任务收尾统一保存其引擎消息）。"""
    store = FakeStore()
    run_msgs = [{"role": "user", "content": "任务消息"}]
    run = SimpleNamespace(
        engine=SimpleNamespace(mutable_messages=run_msgs),
        finished=asyncio.Event(),
    )
    monkeypatch.setattr(server.state, "session_store", store)
    monkeypatch.setattr(server.state, "engine_session_id", "s1")
    monkeypatch.setattr(server.state, "running_runs", {"s1": run})
    _fake_compact(monkeypatch, [{"role": "user", "content": "[摘要]"}])

    result = asyncio.run(run_command({"command": "/compact"}))

    assert "compacted" in result["output"]
    assert store.saved == []


def test_compact_failure_skips_persist(monkeypatch, view_engine):
    """压缩未成功（消息不足等）：不落库。"""
    store = FakeStore()
    monkeypatch.setattr(server.state, "session_store", store)
    monkeypatch.setattr(server.state, "engine_session_id", "s0")
    view_engine.clear()

    result = asyncio.run(run_command({"command": "/compact"}))

    assert "No messages" in result["output"]
    assert store.saved == []
