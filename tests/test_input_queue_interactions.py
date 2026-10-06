"""输入队列交互测试（input-queue-interactions）：队列态/转向/编辑/排序/清空/端点。"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

import server.state
from session.store import SessionStore


@pytest.fixture
def store(workspace, monkeypatch):
    """真实 SessionStore + 干净 server.state 装配（端点直调所需）。"""
    s = SessionStore(db_path=workspace / "sessions.db")
    s.add_workspace(str(workspace))
    monkeypatch.setattr(server.state, "session_store", s)
    monkeypatch.setattr(server.state, "running_runs", {})
    return s


def _body(resp) -> dict:
    """JSONResponse → dict（端点断言用）。"""
    return json.loads(resp.body)


# ---------------------------------------------------------------------------
# store：队列态与转正门
# ---------------------------------------------------------------------------


def test_queue_state_default_open_and_gate_blocks(store):
    sid = "sess_a"
    assert store.get_queue_state(sid) == {"auto_drain": True, "pause_reason": None}
    store.admit_session_input(sid, "sendText", "queue", {"role": "user", "content": "A"})
    store.set_queue_auto_drain(sid, False, "manual")
    assert store.promote_queued_inputs(sid) == []
    assert store.count_queued_inputs(sid) == 1
    store.set_queue_auto_drain(sid, True, None)
    out = store.promote_queued_inputs(sid)
    assert [m["content"] for m in out] == ["A"]
    assert store.get_queue_state(sid)["pause_reason"] is None


def test_promote_guide_priority_and_steer_mark(store):
    sid = "sess_b"
    q1 = store.admit_session_input(sid, "sendText", "queue", {"role": "user", "content": "先入队"})
    store.admit_session_input(sid, "sendText", "queue", {"role": "user", "content": "后入队"})
    assert store.steer_input(q1) == "ok"
    out = store.promote_queued_inputs(sid)
    # guide 优先于同批 queue 行，且转正消息带 _steer
    assert [m["content"] for m in out] == ["先入队", "后入队"]
    assert out[0].get("_steer") is True
    assert "_steer" not in out[1]


# ---------------------------------------------------------------------------
# store：条目操作
# ---------------------------------------------------------------------------


def test_edit_text_only_keeps_order(store):
    sid = "sess_c"
    q1 = store.admit_session_input(sid, "sendText", "queue", {"role": "user", "content": "一"})
    q2 = store.admit_session_input(sid, "sendText", "queue", {"role": "user", "content": [
        {"type": "text", "text": "t"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}},
    ]})
    assert store.edit_queued_input(q1, "一改") is True
    assert store.edit_queued_input(q2, "不该成功") is False
    items = store.list_queued_inputs(sid)
    assert [i["id"] for i in items] == [q1, q2]  # 保序
    assert items[0]["content"] == "一改"
    # 已转正行不可编辑
    store.promote_queued_inputs(sid)
    assert store.edit_queued_input(q1, "再改") is False


def test_reorder_before_insert_append_end_and_invalid(store):
    sid = "sess_d"
    ids = [store.admit_session_input(sid, "sendText", "queue", {"role": "user", "content": c})
            for c in ("1", "2", "3")]
    assert store.reorder_input(ids[2], ids[0]) is True
    assert [i["content"] for i in store.list_queued_inputs(sid)] == ["3", "1", "2"]
    # before_id 空 = 追加末尾（对齐目标语义）
    assert store.reorder_input(ids[2], None) is True
    assert [i["content"] for i in store.list_queued_inputs(sid)] == ["1", "2", "3"]
    # 非法目标：before 不是 queued 行
    assert store.reorder_input(ids[0], "queue_不存在") is False
    # 重排后转正按新序
    out = store.promote_queued_inputs(sid)
    assert [m["content"] for m in out] == ["1", "2", "3"]


def test_steer_input_paths(store):
    sid = "sess_e"
    q1 = store.admit_session_input(sid, "sendText", "queue", {"role": "user", "content": "文本"})
    q2 = store.admit_session_input(sid, "sendText", "queue", {"role": "user", "content": [
        {"type": "text", "text": "t"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}},
    ]})
    assert store.steer_input(q2) == "attachments_unsupported"
    assert store.steer_input(q1) == "ok"
    assert store.steer_input(q1) == "ok"  # 重复转向幂等：仍是 queued 的 guide 行
    assert store.steer_input("queue_不存在") == "not_queued"
    # 图片行保持 queue 原位
    items = {i["id"]: i["delivery"] for i in store.list_queued_inputs(sid)}
    assert items[q2] == "queue"


def test_clear_then_cancel_false(store):
    sid = "sess_f"
    q1 = store.admit_session_input(sid, "sendText", "queue", {"role": "user", "content": "A"})
    store.admit_session_input(sid, "backgroundNotification", "queue", {"role": "user", "content": "N"})
    assert store.clear_queued_inputs(sid) == 2
    assert store.list_queued_inputs(sid) == []
    # 清空后对已清行撤销返回 False（不复活、不覆写）
    assert store.cancel_session_input(q1) is False


def test_take_single_and_tail(store):
    sid = "sess_g"
    store.admit_session_input(sid, "sendText", "queue", {"role": "user", "content": "头"})
    q2 = store.admit_session_input(sid, "sendText", "queue", {"role": "user", "content": "尾"})
    tail = store.tail_queued_input(sid)
    assert tail["id"] == q2
    msg = store.take_input_for_now(q2)
    assert msg["content"] == "尾" and msg["_input_id"] == q2
    assert store.take_input_for_now(q2) is None
    assert store.count_queued_inputs(sid) == 1  # 只取出目标行
    # guide 行 take 带 _steer
    head_id = store.list_queued_inputs(sid)[0]["id"]
    store.steer_input(head_id)
    m2 = store.take_input_for_now(head_id)
    assert m2.get("_steer") is True


def test_reorder_self_target_is_noop(store):
    """目标指向自身：无操作返回 True，不抛错不重排（陈旧拖拽态/直调端点防御）。"""
    sid = "sess_j"
    ids = [store.admit_session_input(sid, "sendText", "queue", {"role": "user", "content": c})
           for c in ("1", "2")]
    assert store.reorder_input(ids[0], ids[0]) is True
    assert [i["id"] for i in store.list_queued_inputs(sid)] == ids


# ---------------------------------------------------------------------------
# 端点：steer / pause / auto_pause 函数 / GET 扩展
# ---------------------------------------------------------------------------


def test_steer_endpoint_running_sets_guide(store, workspace):
    from server.routers.chat.routes import steer_session_input

    sid = store.create_session(str(workspace)).id
    q = store.admit_session_input(sid, "sendText", "queue", {"role": "user", "content": "排队"})
    server.state.running_runs[sid] = object()
    resp = steer_session_input({"id": q})
    assert resp == {"ok": True, "mode": "guide"}
    items = store.list_queued_inputs(sid)
    assert items[0]["delivery"] == "guide"
    # 队列条语义：guide 行仍在表内（前端分流排除，store 层不隐藏）


def test_steer_endpoint_image_rejected(store, workspace):
    from server.routers.chat.routes import steer_session_input
    from starlette.responses import JSONResponse

    sid = store.create_session(str(workspace)).id
    q = store.admit_session_input(sid, "sendText", "queue", {"role": "user", "content": [
        {"type": "text", "text": "t"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}},
    ]})
    server.state.running_runs[sid] = object()
    resp = steer_session_input({"id": q})
    assert isinstance(resp, JSONResponse)
    assert resp.status_code == 400
    assert "附件" in _body(resp)["error"]


class _FakeEngine:
    def __init__(self):
        self.mutable_messages: list = []
        self.release = asyncio.Event()
        self.release.set()

    async def submitMessage(self, prompt, user_context=None, system_context=None, message_meta=None):
        msg: dict = {"role": "user", "content": prompt, "_ts": 1}
        if message_meta:
            msg.update(message_meta)
        self.mutable_messages.append(msg)
        await self.release.wait()
        yield {"role": "assistant", "content": "回复"}


def _wire_run_env(monkeypatch, store, workspace, engine_box):
    """装配 _start_run 所需 server.state（与 test_interrupt_flush_image_read 同款）。"""
    from server.permission_bridge import PermissionBridge
    from server.question_bridge import QuestionBridge

    class FakeAppState:
        def get_state(self):
            return SimpleNamespace(
                token_usage=SimpleNamespace(
                    input_tokens=0, output_tokens=0, cache_read_input_tokens=0,
                    cache_creation_input_tokens=0, last_prompt_tokens=0,
                    last_cache_creation=0, total_input_tokens=0,
                ),
                model="test-model", total_cost_usd=0.0,
            )

    from query.engine import QueryEngineConfig

    def fake_qe(config, initial_messages=None, session_id=""):
        e = _FakeEngine()
        e.mutable_messages = list(initial_messages or [])
        engine_box["engine"] = e
        return e

    monkeypatch.setattr(server.state, "running_runs", {})
    monkeypatch.setattr(server.state, "engine", _FakeEngine())
    monkeypatch.setattr(server.state, "permission_bridge", PermissionBridge())
    monkeypatch.setattr(server.state, "question_bridge", QuestionBridge())
    monkeypatch.setattr(server.state, "app_state", FakeAppState())
    monkeypatch.setattr(server.state, "engine_session_id", None)
    monkeypatch.setattr(server.state, "stream_finalize_timeout", 0.2)
    from server.routers.chat import routes as chat_routes

    monkeypatch.setattr(chat_routes, "QueryEngine", fake_qe)
    monkeypatch.setattr(chat_routes, "build_engine_config", lambda **kw: QueryEngineConfig())


@pytest.mark.asyncio
async def test_steer_endpoint_idle_starts_run_with_fifo(store, workspace, monkeypatch):
    """空闲「立即」：目标行作本轮 prompt 排最后，其余 queued 行按序先行注入。"""
    from server.routers.chat.routes import steer_session_input

    sid = store.create_session(str(workspace)).id
    store.save_messages(sid, [{"role": "user", "content": "旧问题"}, {"role": "assistant", "content": "旧回答"}])
    qa = store.admit_session_input(sid, "sendText", "queue", {"role": "user", "content": "A"})
    qb = store.admit_session_input(sid, "sendText", "queue", {"role": "user", "content": "B"})
    qc = store.admit_session_input(sid, "sendText", "queue", {"role": "user", "content": "C"})
    box: dict = {}
    _wire_run_env(monkeypatch, store, workspace, box)

    resp = steer_session_input({"id": qb})
    assert resp["ok"] is True and resp["mode"] == "started"
    engine = box["engine"]
    # 起轮任务异步把本轮 prompt 追加进引擎：等到第 5 条（前缀 4 条同步就位）
    for _ in range(100):
        if len(engine.mutable_messages) >= 5:
            break
        await asyncio.sleep(0.02)
    contents = [m.get("content") for m in engine.mutable_messages]
    # 历史 → A、C 先行转正 → B 作本轮 prompt 最后
    assert contents == ["旧问题", "旧回答", "A", "C", "B"]
    prompt_msg = engine.mutable_messages[-1]
    assert prompt_msg.get("_input_id") == qb
    assert "_steer" not in prompt_msg
    assert store.count_queued_inputs(sid) == 0


@pytest.mark.asyncio
async def test_pause_blocks_promotion_and_resume_starts_tail(store, workspace, monkeypatch):
    """暂停挡转正；恢复且空闲队列非空 → 队尾行起轮、其余先行。"""
    from server.routers.chat.routes import pause_session_inputs

    sid = store.create_session(str(workspace)).id
    store.save_messages(sid, [])
    qa = store.admit_session_input(sid, "sendText", "queue", {"role": "user", "content": "甲"})
    qb = store.admit_session_input(sid, "sendText", "queue", {"role": "user", "content": "乙"})

    r = pause_session_inputs({"session_id": sid, "paused": True})
    assert r == {"ok": True, "auto_drain": False, "resumed_run": False}
    assert store.promote_queued_inputs(sid) == []  # 暂停挡转正（起轮点位同门）

    box: dict = {}
    _wire_run_env(monkeypatch, store, workspace, box)
    r2 = pause_session_inputs({"session_id": sid, "paused": False})
    assert r2["resumed_run"] is True
    engine = box["engine"]
    # 前缀（甲）同步就位，本轮 prompt（乙）由起轮任务追加：等两条齐
    for _ in range(100):
        if len(engine.mutable_messages) >= 2:
            break
        await asyncio.sleep(0.02)
    contents = [m.get("content") for m in engine.mutable_messages]
    assert contents == ["甲", "乙"]  # 队尾行「乙」作 prompt，「甲」先行转正
    assert engine.mutable_messages[-1]["_input_id"] == qb
    assert qa and store.count_queued_inputs(sid) == 0


def test_pause_resume_no_run_when_empty_or_running(store, workspace):
    from server.routers.chat.routes import pause_session_inputs

    sid = store.create_session(str(workspace)).id
    # 空队列恢复：不起轮
    assert pause_session_inputs({"session_id": sid, "paused": False}) == {
        "ok": True, "auto_drain": True, "resumed_run": False,
    }
    # 有运行任务：不起轮（边界自然消化）
    store.admit_session_input(sid, "sendText", "queue", {"role": "user", "content": "X"})
    server.state.running_runs[sid] = object()
    assert pause_session_inputs({"session_id": sid, "paused": False})["resumed_run"] is False


def test_auto_pause_queue_function(store):
    from server.routers.chat.routes import _auto_pause_queue

    sid = "sess_h"
    # 无残留行：不动
    _auto_pause_queue(store, sid, "aborted")
    assert store.get_queue_state(sid)["auto_drain"] is True
    store.admit_session_input(sid, "sendText", "queue", {"role": "user", "content": "残留"})
    # completed 不动
    _auto_pause_queue(store, sid, "completed")
    assert store.get_queue_state(sid)["auto_drain"] is True
    # aborted → stopped
    _auto_pause_queue(store, sid, "aborted")
    st = store.get_queue_state(sid)
    assert st["auto_drain"] is False and st["pause_reason"] == "stopped"
    # error 不覆写 manual：重置场景
    sid2 = "sess_i"
    store.admit_session_input(sid2, "sendText", "queue", {"role": "user", "content": "残留"})
    store.set_queue_auto_drain(sid2, False, "manual")
    _auto_pause_queue(store, sid2, "error")
    assert store.get_queue_state(sid2)["pause_reason"] == "manual"


def test_get_endpoint_returns_queue_state(store, workspace):
    from server.routers.chat.routes import list_session_inputs

    sid = store.create_session(str(workspace)).id
    q = store.admit_session_input(sid, "sendText", "queue", {"role": "user", "content": "A"})
    store.steer_input(q)
    store.set_queue_auto_drain(sid, False, "manual")
    out = list_session_inputs(session_id=sid)
    assert out["queue_state"] == {"auto_drain": False, "pause_reason": "manual"}
    assert out["items"][0]["delivery"] == "guide"


@pytest.mark.asyncio
async def test_steer_endpoint_idle_guide_row_prompt_carries_steer(store, workspace, monkeypatch):
    """guide 行在空闲态点「立即」：以该行起轮，本轮 prompt 消息补 `_steer`。"""
    from server.routers.chat.routes import steer_session_input

    sid = store.create_session(str(workspace)).id
    store.save_messages(sid, [])
    q = store.admit_session_input(sid, "sendText", "queue", {"role": "user", "content": "转向行"})
    # 先在有运行任务时置 guide
    server.state.running_runs[sid] = object()
    assert steer_session_input({"id": q}) == {"ok": True, "mode": "guide"}
    server.state.running_runs.pop(sid)
    # 再空闲点立即 → 起轮，prompt 带 _steer
    box: dict = {}
    _wire_run_env(monkeypatch, store, workspace, box)
    resp = steer_session_input({"id": q})
    assert resp["ok"] is True and resp["mode"] == "started"
    engine = box["engine"]
    for _ in range(100):
        if engine.mutable_messages:
            break
        await asyncio.sleep(0.02)
    last = engine.mutable_messages[-1]
    assert last["content"] == "转向行"
    assert last.get("_steer") is True


@pytest.mark.asyncio
async def test_pause_resume_ignores_auto_resume_switch(store, workspace, monkeypatch):
    """恢复起轮是用户显式操作：通知自动续跑开关关闭时同样生效。"""
    from server.routers.chat.routes import pause_session_inputs
    from startup.config import GlobalConfig
    from startup.config.types import SubagentsConfig

    sid = store.create_session(str(workspace)).id
    store.save_messages(sid, [])
    store.admit_session_input(sid, "sendText", "queue", {"role": "user", "content": "残留"})
    monkeypatch.setattr(
        "startup.config.get_global_config",
        lambda: GlobalConfig(subagents=SubagentsConfig(auto_resume_parent=False)),
    )
    box: dict = {}
    _wire_run_env(monkeypatch, store, workspace, box)
    r = pause_session_inputs({"session_id": sid, "paused": False})
    assert r["resumed_run"] is True
    assert store.count_queued_inputs(sid) == 0
    await asyncio.sleep(0.1)  # 让起轮任务收尾，避免悬挂协程告警
