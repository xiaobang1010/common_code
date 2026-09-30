"""compact 命令 HTTP 接线测试。

验证 /api/command 的 compact 分支注入引擎真实消息与压缩函数后
能真实执行压缩（手动 keep=0 全量摘要、插入式边界语义），且其余命令
（/clear）行为不变、运行中任务优先取任务引擎消息。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import query.services.compact.auto_compact as auto_compact
import server.state
from server.routers.commands.routes import run_command
from startup.state.app_state import AppState, AppStateProvider


def _fake_compact(monkeypatch, produced: list[dict]) -> None:
    """把 compact_conversation 打桩为固定返回，避免真实 LLM 调用。

    produced 表示压缩产出的插入部分（boundary marker + 摘要消息），
    模拟手动 keep=0 的插入语义：返回原消息 + 插入项。
    """

    async def fake(
        messages: list[dict],
        model: str,
        keep_groups: int = 0,
        transcript_path: str | None = None,
        custom_instructions: str | None = None,
    ) -> list[dict]:
        # 插入语义：保留全量 + 追加边界/摘要（keep=0 时原消息仍在库）
        return [*messages, *produced]

    monkeypatch.setattr(auto_compact, "compact_conversation", fake)


def _insertion_products() -> list[dict]:
    """构造一次手动压缩产出的插入项（边界标记 + 带标记摘要）。"""
    return [
        {"role": "system", "content": "[Compact Boundary — auto — pre-compact tokens: 3]"},
        {"role": "user", "content": "[摘要]", "_compact_summary": True},
    ]


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
    """非空会话：compact 作用于引擎真实消息，全量保留并插入边界与摘要。"""
    _fake_compact(monkeypatch, _insertion_products())

    result = asyncio.run(run_command({"command": "/compact"}))

    assert "compacted" in result["output"]
    # 插入语义：原 3 条全量保留 + 边界 + 摘要
    assert len(view_engine) == 5
    assert view_engine[3]["content"].startswith("[Compact Boundary")
    assert view_engine[4]["_compact_summary"] is True
    assert view_engine[4]["content"] == "[摘要]"


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
    _fake_compact(monkeypatch, _insertion_products())

    result = asyncio.run(run_command({"command": "/compact"}))

    assert "compacted" in result["output"]
    contents = [m["content"] for m in run_msgs]
    assert contents[0] == "任务消息"
    assert contents[2] == "[摘要]"
    # 查看引擎消息未被误动
    assert len(view_engine) == 3


def test_clear_does_not_touch_engine_messages(monkeypatch, view_engine):
    """/clear 维持原行为：不注入引擎消息，HTTP 侧仍为空操作。"""
    result = asyncio.run(run_command({"command": "/clear"}))

    assert "output" in result
    assert len(view_engine) == 3


class FakeStore:
    """记录 save_messages / export_transcript 调用的假会话存储。"""

    def __init__(self) -> None:
        self.saved: list[tuple[str, list]] = []
        self.exported: list[tuple[str, list]] = []

    def save_messages(self, session_id: str, messages: list) -> None:
        self.saved.append((session_id, list(messages)))

    def export_transcript(self, session_id: str, messages: list) -> str:
        self.exported.append((session_id, list(messages)))
        return f"~/.agent/transcripts/{session_id}.jsonl"


def test_compact_success_persists_to_store(monkeypatch, view_engine):
    """空闲查看引擎压缩成功：全量结果落库并导出转录，防止下一轮回灌旧窗口。"""
    store = FakeStore()
    monkeypatch.setattr(server.state, "session_store", store)
    monkeypatch.setattr(server.state, "engine_session_id", "s0")
    _fake_compact(monkeypatch, _insertion_products())

    result = asyncio.run(run_command({"command": "/compact"}))

    assert "compacted" in result["output"]
    assert len(store.saved) == 1 and store.saved[0][0] == "s0"
    saved_msgs = store.saved[0][1]
    # 插入语义：DB 保留全量历史（原 3 条 + 边界 + 摘要）
    assert len(saved_msgs) == 5
    assert saved_msgs[3]["content"].startswith("[Compact Boundary")
    assert saved_msgs[4]["_compact_summary"] is True
    # 转录与落库同内容导出（压缩逃生门）
    assert len(store.exported) == 1 and store.exported[0][0] == "s0"


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
