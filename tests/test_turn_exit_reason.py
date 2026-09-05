"""回合退出原因（last_turn）落库与透出测试。

直接驱动 chat_event_stream 生成器（复用 test_chat_session_binding 的装配方式），
FakeEngine 按真实引擎在回合内追加 user 消息并打 _ts，验证：
- LoopResult 四态 / abort / 引擎异常 各自落库的 reason 与 error 摘要（截断 500）
- user_ts 与落库列表最后一条可见 user 消息 _ts 同源精确相等；连续两回合指向第二回合
- hook 拦截形态：归属确认拒绝上一回合消息，不写 user_ts 键，上一回合数据不被改写
- /api/state、会话详情、switch 三接口透出 last_turn
- 旧库补列幂等、set/get 往返、写失败不影响收尾
- 重试配置默认值对齐（10 次 / 2s / 60s）与 Anthropic 路径逐次 phase 反馈
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

import server.state
from query.loop import LoopResult
from query.services.api.with_retry import RetryConfig
from server.permission_bridge import PermissionBridge
from server.question_bridge import QuestionBridge
from server.routers.chat.routes import chat_event_stream, get_state
from server.routers.sessions.routes import get_session as session_detail_route
from server.routers.sessions.routes import switch_session
from session.store import SessionStore

PROMPT = "现在的工作目录是哪里？给我路径"


class FakeEngine:
    """假引擎：mutable_messages + submitMessage 异步生成器。

    mode：
      - "turn"：正常回合，追加 user/assistant 后产出 LoopResult
      - "hook_blocked"：模拟 user_prompt_submit hook 拦截——本回合 user
        不进引擎列表、不产出任何事件（真实引擎在追加前 return）
      - "crash"：进入后抛异常（无 LoopResult）
    emit_assistant=False 时只落 user（模型出错无产出的真实形态）。
    """

    def __init__(self) -> None:
        self.mutable_messages: list = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.mode = "turn"
        self.emit_assistant = True
        self.loop_result: LoopResult = LoopResult(reason="completed")

    async def submitMessage(self, prompt, user_context=None, system_context=None):
        if self.mode == "hook_blocked":
            self.started.set()
            await self.release.wait()
            return
        # 与真实引擎一致：在回合内（晚于 run.started_at）追加 user 并打 _ts
        self.mutable_messages.append(
            {"role": "user", "content": prompt, "_ts": time.time() * 1000}
        )
        self.started.set()
        await self.release.wait()
        if self.mode == "crash":
            raise RuntimeError("引擎炸了")
        if self.emit_assistant:
            self.mutable_messages.append({"role": "assistant", "content": "回复内容"})
            yield {"role": "assistant", "content": "回复内容"}
        yield self.loop_result


class FakeAppState:
    """假 AppState：token 累加用。"""

    def get_state(self):
        return SimpleNamespace(
            token_usage=SimpleNamespace(
                input_tokens=0,
                output_tokens=0,
                cache_read_input_tokens=0,
                cache_creation_input_tokens=0,
                total_input_tokens=0,
                last_prompt_tokens=0,
                last_cache_creation=0,
            ),
            model="test-model",
            total_cost_usd=0.0,
            context_breakdown={},
        )


@pytest.fixture
def env(workspace, monkeypatch):
    """装配 server.state：FakeEngine + 真实 SessionStore + 真实桥（同 binding 测试风格）。"""
    from server.routers.chat import routes as chat_routes

    store = SessionStore(db_path=workspace / "sessions.db")
    engine = FakeEngine()
    monkeypatch.setattr(server.state, "engine", engine)
    monkeypatch.setattr(server.state, "running_runs", {})

    def fake_query_engine(config, initial_messages=None, session_id=""):
        engine.mutable_messages = list(initial_messages or [])
        return engine

    monkeypatch.setattr(chat_routes, "QueryEngine", fake_query_engine)
    from query.engine import QueryEngineConfig

    monkeypatch.setattr(chat_routes, "build_engine_config", lambda **kw: QueryEngineConfig())
    server.state.running_runs.clear()
    monkeypatch.setattr(server.state, "session_store", store)
    monkeypatch.setattr(server.state, "permission_bridge", PermissionBridge())
    monkeypatch.setattr(server.state, "question_bridge", QuestionBridge())
    monkeypatch.setattr(server.state, "app_state", FakeAppState())
    monkeypatch.setattr(server.state, "engine_session_id", None)
    monkeypatch.setattr(server.state, "stream_finalize_timeout", 0.2)
    return engine, store


async def collect(gen):
    """消费 SSE 生成器，返回解析后的事件列表。"""
    events = []
    async for chunk in gen:
        line = chunk.split("data: ", 1)[1].strip()
        events.append(json.loads(line))
    return events


def _new_session(store, workspace):
    store.add_workspace(str(workspace))
    return store.create_session(str(workspace)).id


# --- LoopResult 各态落库 ---


@pytest.mark.asyncio
async def test_completed_turn_writes_last_turn(workspace, env):
    engine, store = env
    sid = _new_session(store, workspace)
    engine.release.set()

    await collect(chat_event_stream(PROMPT, sid))

    lt = store.get_session(sid).last_turn
    assert lt["reason"] == "completed"
    assert isinstance(lt["finished_at"], (int, float))
    # user_ts 与落库列表最后一条可见 user 消息 _ts 同源精确相等
    db_users = [m for m in store.get_session(sid).messages if m.get("role") == "user"]
    assert lt["user_ts"] == db_users[-1]["_ts"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reason,with_error",
    [
        ("completed", False),
        ("model_error", True),
        ("prompt_too_long", True),
        ("max_output_tokens_exhausted", False),
    ],
)
async def test_loop_result_reasons_persisted(workspace, env, reason, with_error):
    """LoopResult 四态参数化：reason 原样落库，error 摘要截断 500 字符。"""
    engine, store = env
    sid = _new_session(store, workspace)
    engine.emit_assistant = False  # 异常态无 assistant 产出（真实故障形态）
    engine.loop_result = LoopResult(
        reason=reason, error=Exception("x" * 600) if with_error else None
    )
    engine.release.set()

    await collect(chat_event_stream(PROMPT, sid))

    lt = store.get_session(sid).last_turn
    assert lt["reason"] == reason
    if with_error:
        assert len(lt["error"]) == 500
    else:
        assert "error" not in lt


@pytest.mark.asyncio
async def test_abort_writes_aborted(workspace, env):
    """置位 abort_event 后 cancel（/api/abort 同款动作）：无 LoopResult 也落 aborted。"""
    engine, store = env
    sid = _new_session(store, workspace)

    consumer = asyncio.create_task(collect(chat_event_stream(PROMPT, sid)))
    await engine.started.wait()
    run = server.state.running_runs[sid]
    run.abort_event.set()
    run.task.cancel()
    await consumer

    lt = store.get_session(sid).last_turn
    assert lt["reason"] == "aborted"
    # user 消息在 cancel 前已入引擎列表，user_ts 归属确认通过
    assert "user_ts" in lt


@pytest.mark.asyncio
async def test_engine_crash_writes_error(workspace, env):
    """引擎抛异常（无 LoopResult）：落 error + 异常摘要，SSE 透出 error 事件。"""
    engine, store = env
    sid = _new_session(store, workspace)
    engine.mode = "crash"
    engine.release.set()

    events = await collect(chat_event_stream(PROMPT, sid))

    lt = store.get_session(sid).last_turn
    assert lt["reason"] == "error"
    assert "引擎炸了" in lt["error"]
    assert any(e.get("type") == "error" for e in events)


# --- user_ts 归属确认 ---


@pytest.mark.asyncio
async def test_two_turns_user_ts_points_to_second(workspace, env):
    """连续两回合：last_turn 的 user_ts 指向第二回合消息，不残留第一回合值。"""
    engine, store = env
    sid = _new_session(store, workspace)
    engine.release.set()
    await collect(chat_event_stream(PROMPT, sid))
    first_ts = store.get_session(sid).last_turn["user_ts"]

    await collect(chat_event_stream("第二回合", sid))

    lt = store.get_session(sid).last_turn
    assert lt["user_ts"] != first_ts
    db_users = [m for m in store.get_session(sid).messages if m.get("role") == "user"]
    assert lt["user_ts"] == db_users[-1]["_ts"]


@pytest.mark.asyncio
async def test_hook_blocked_omits_user_ts_and_keeps_previous_turn(workspace, env):
    """hook 拦截本回合 user：取到的是上一回合消息 → 归属确认拒绝，不写 user_ts 键；
    上一回合的落库数据不被改写。"""
    engine, store = env
    sid = _new_session(store, workspace)
    engine.release.set()
    await collect(chat_event_stream(PROMPT, sid))
    first_snapshot = store.get_session(sid).messages

    engine.mode = "hook_blocked"
    await collect(chat_event_stream("会被拦截的消息", sid))

    lt = store.get_session(sid).last_turn
    assert lt["reason"] == "error"
    assert "user_ts" not in lt
    # 上一回合消息原样保留（本回合无产出，收尾保存的前缀即第一回合列表）
    assert store.get_session(sid).messages == first_snapshot


# --- 三接口透出 ---


@pytest.mark.asyncio
async def test_state_detail_switch_expose_last_turn(workspace, env):
    engine, store = env
    sid = _new_session(store, workspace)
    engine.loop_result = LoopResult(reason="model_error", error=Exception("APIConnectionError"))
    engine.emit_assistant = False
    engine.release.set()
    await collect(chat_event_stream(PROMPT, sid))

    state = await get_state()
    assert state["last_turn"]["reason"] == "model_error"

    detail = session_detail_route(sid)
    assert detail["session"]["last_turn"]["reason"] == "model_error"

    switched = switch_session(sid)
    assert switched["last_turn"]["reason"] == "model_error"


# --- store 层：补列迁移与读写 ---


def test_old_db_migration_adds_column(tmp_path):
    """旧库（无 last_turn 列）自动补列，set/get 往返正常，重复 _init_schema 幂等。"""
    db = tmp_path / "old.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        """
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            workspace_path TEXT NOT NULL,
            title TEXT DEFAULT '',
            branch TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            messages TEXT DEFAULT '[]'
        )
        """
    )
    conn.execute("INSERT INTO sessions (id, workspace_path, created_at, updated_at) VALUES ('s1', 'ws', 'a', 'b')")
    conn.commit()
    conn.close()

    store = SessionStore(db_path=db)
    assert store.set_session_last_turn("s1", {"reason": "model_error", "error": "x"}) is True
    assert store.get_session_last_turn("s1") == {"reason": "model_error", "error": "x"}
    # 重复初始化幂等，读回不变
    store._init_schema()
    assert store.get_session_last_turn("s1") == {"reason": "model_error", "error": "x"}


def test_get_last_turn_guards(tmp_path):
    """None / 不存在的会话 / 坏 JSON 均安全返回 {}；set 未命中返回 False。"""
    store = SessionStore(db_path=tmp_path / "s.db")
    assert store.get_session_last_turn(None) == {}
    assert store.get_session_last_turn("nope") == {}
    assert store.set_session_last_turn("nope", {"reason": "completed"}) is False
    sid = store.create_session("ws").id
    store.save_messages(sid, [])
    conn = sqlite3.connect(str(tmp_path / "s.db"))
    conn.execute("UPDATE sessions SET last_turn = '坏JSON{' WHERE id = ?", (sid,))
    conn.commit()
    conn.close()
    assert store.get_session_last_turn(sid) == {}


@pytest.mark.asyncio
async def test_set_last_turn_failure_does_not_break_finalize(workspace, env, monkeypatch):
    """写 last_turn 抛异常：消息照常落库、注册表清理、收尾完成（finished 置位）。"""
    engine, store = env
    sid = _new_session(store, workspace)

    def boom(session_id, meta):
        raise RuntimeError("写库失败")

    monkeypatch.setattr(store, "set_session_last_turn", boom)
    engine.release.set()
    await collect(chat_event_stream(PROMPT, sid))  # collect 返回即 finished 已置位

    session = store.get_session(sid)
    assert any(m.get("role") == "assistant" for m in session.messages)
    assert session.last_turn == {}
    assert sid not in server.state.running_runs


# --- 重试策略对齐 ---


def test_retry_config_defaults_aligned():
    """默认值为唯一事实源：10 次重试、2s 基础退避、封顶 60s。"""
    cfg = RetryConfig()
    assert cfg.max_retries == 10
    assert cfg.base_delay == 2.0
    assert cfg.max_delay == 60.0


def test_openai_path_no_longer_overrides_retries():
    """OpenAI 路径不再自带 3 次覆写（走 RetryConfig 默认）。"""
    src = (Path(__file__).resolve().parents[1] / "query" / "services" / "api" / "llm.py").read_text(
        encoding="utf-8"
    )
    assert "max_retries=3" not in src


class _FlakyHandler(BaseHTTPRequestHandler):
    """首次请求返回 500（可重试），第二次返回正常 SSE 流。"""

    hits = 0

    def do_POST(self):  # noqa: N802
        type(self).hits += 1
        if self.hits == 1:
            body = b'{"type":"error","error":{"type":"api_error","message":"boom"}}'
            self.send_response(500)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        sse = (
            'data: {"type":"message_start","message":{"usage":{}}}\n\n'
            'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"好"}}\n\n'
            'data: {"type":"message_stop"}\n\n'
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(sse)))
        self.end_headers()
        self.wfile.write(sse)

    def log_message(self, *args):  # 静默
        pass


@pytest.mark.asyncio
async def test_anthropic_retry_emits_phase_event(monkeypatch):
    """Anthropic 路径重试时产出逐次 phase 事件（前端不再静默）。"""
    import query.services.api.anthropic_llm as al
    import startup.config

    # 函数内 import：patch 模块属性即可；绕开真实配置文件读取守卫
    monkeypatch.setattr(
        startup.config, "get_global_config",
        lambda: SimpleNamespace(prompt_cache_enabled=False),
    )
    _FlakyHandler.hits = 0
    server = HTTPServer(("127.0.0.1", 0), _FlakyHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setattr(
            al, "_get_anthropic_config",
            lambda: (f"http://127.0.0.1:{port}", "test-key", "test-model"),
        )
        # 小退避加速：重试行为与事件形态不受参数影响
        monkeypatch.setattr(
            al,
            "RetryConfig",
            lambda: SimpleNamespace(
                max_retries=10, base_delay=0.01, max_delay=0.02,
                retryable_errors={"rate_limit", "server_error"},
            ),
        )
        events = []
        async for ev in al.query_model_with_streaming_anthropic(
            [{"role": "user", "content": "hi"}]
        ):
            events.append(ev)
    finally:
        server.shutdown()

    phases = [e for e in events if e.type == "phase" and "正在重试 1/10" in (e.content or "")]
    assert phases, f"未产出逐次重试 phase 事件：{[(e.type, e.content) for e in events]}"
    # 重试后最终拿到内容
    assert any(e.type == "content" for e in events)
