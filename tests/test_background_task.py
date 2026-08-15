"""后台任务模型测试：任务与连接解耦、并发隔离、串行约束、回写视图、cwd 隔离等。

复用 chat-session-binding 的装配模式：chat 路由的 QueryEngine 被替换为
FakeEngine（每次调用新建实例并记录到 engine.instances），真实 SessionStore。
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from starlette.requests import Request

import server.state
from server.routers.chat.routes import abort_query, chat_event_stream, get_state
from server.permission_bridge import PermissionBridge
from server.question_bridge import QuestionBridge
from session.store import SessionStore


class FakeEngine:
    """假任务引擎：记录任务上下文里的 workspace_var，started/release 控制产出。"""

    def __init__(self, messages: list | None = None) -> None:
        self.mutable_messages: list = messages or []
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        # 任务上下文里观测到的工作区（cwd 隔离断言用）
        self.observed_workspace: str | None = None

    async def submitMessage(self, prompt, user_context=None, system_context=None):
        # 记录任务执行上下文里的工作区（workspace_var 由任务协程设置）
        self.observed_workspace = server.state.workspace_var.get()
        self.mutable_messages.append({"role": "user", "content": prompt})
        self.started.set()
        await self.release.wait()
        self.mutable_messages.append({"role": "assistant", "content": "回复内容"})
        yield {"role": "assistant", "content": "回复内容"}


class FakeAppState:
    """假 AppState：token 累加用。"""

    def get_state(self):
        return SimpleNamespace(
            token_usage=SimpleNamespace(
                input_tokens=0,
                output_tokens=0,
                cache_read_input_tokens=0,
                cache_creation_input_tokens=0,
                last_prompt_tokens=0,
                last_cache_creation=0,
            ),
            model="test-model",
            total_cost_usd=0.0,
        )


@pytest.fixture
def env(workspace, monkeypatch):
    """装配 server.state：FakeEngine 任务引擎（每流一个实例）+ 真实存储与桥。"""
    from query.engine import QueryEngineConfig
    from server.routers.chat import routes as chat_routes

    store = SessionStore(db_path=workspace / "sessions.db")
    # 查看视图引擎；任务引擎实例记录在 engine.instances（每流一个）
    engine = FakeEngine()
    engine.instances: list[FakeEngine] = []
    monkeypatch.setattr(server.state, "session_store", store)
    monkeypatch.setattr(server.state, "engine", engine)
    monkeypatch.setattr(server.state, "running_runs", {})
    monkeypatch.setattr(server.state, "permission_bridge", PermissionBridge())
    monkeypatch.setattr(server.state, "question_bridge", QuestionBridge())
    monkeypatch.setattr(server.state, "app_state", FakeAppState())
    monkeypatch.setattr(server.state, "engine_session_id", None)

    def fake_query_engine(config, initial_messages=None):
        inst = FakeEngine(initial_messages or [])
        engine.instances.append(inst)
        return inst

    monkeypatch.setattr(chat_routes, "QueryEngine", fake_query_engine)
    monkeypatch.setattr(chat_routes, "build_engine_config", lambda **kw: QueryEngineConfig())
    server.state.running_runs.clear()
    return engine, store


async def collect(gen):
    """消费 SSE 生成器，返回解析后的事件列表。"""
    events = []
    async for chunk in gen:
        line = chunk.split("data: ", 1)[1].strip()
        events.append(json.loads(line))
    return events


async def get_inst(engine, index: int = 0) -> FakeEngine:
    """等待并返回第 index 个任务引擎实例（协程调度后才创建）。"""
    for _ in range(200):
        if len(engine.instances) > index:
            return engine.instances[index]
        await asyncio.sleep(0.01)
    raise AssertionError(f"task engine #{index} not created")


async def make_request(body: dict) -> Request:
    """构造带 JSON body 的 starlette Request（abort 接口测试用）。"""
    queue: asyncio.Queue = asyncio.Queue()
    await queue.put({"type": "http.request", "body": json.dumps(body).encode(), "more_body": False})
    return Request(
        {"type": "http", "method": "POST", "path": "/api/abort", "headers": []},
        receive=queue.get,
    )


# --- 任务与连接解耦 ---


@pytest.mark.asyncio
async def test_task_continues_after_subscriber_disconnect(workspace, env):
    """断开 SSE 不取消任务：任务后台继续跑完并保存到绑定会话。"""
    engine, store = env
    store.add_workspace(str(workspace))
    sid = store.create_session(str(workspace)).id

    consumer = asyncio.create_task(collect(chat_event_stream("消息", sid)))
    inst = await get_inst(engine, 0)
    await inst.started.wait()

    # 模拟前端断开：取消生成器（任务不应被打断）
    consumer.cancel()
    try:
        await consumer
    except asyncio.CancelledError:
        pass
    assert sid in server.state.running_runs  # 任务仍在后台

    # 放行任务完成
    inst.release.set()
    await server.state.running_runs[sid].finished.wait()

    # 收尾保存完整消息（含 assistant），任务移出注册表
    session = store.get_session(sid)
    assert any(m.get("role") == "assistant" for m in session.messages)
    assert sid not in server.state.running_runs


@pytest.mark.asyncio
async def test_concurrent_sessions_isolated(workspace, env):
    """两会话并发任务：独立引擎与消息缓冲，互不串写，各自保存。"""
    engine, store = env
    store.add_workspace(str(workspace))
    sid_a = store.create_session(str(workspace)).id
    sid_b = store.create_session(str(workspace)).id

    consumer_a = asyncio.create_task(collect(chat_event_stream("A消息", sid_a)))
    inst_a = await get_inst(engine, 0)
    await inst_a.started.wait()

    consumer_b = asyncio.create_task(collect(chat_event_stream("B消息", sid_b)))
    inst_b = await get_inst(engine, 1)
    await inst_b.started.wait()

    # 放行 B，B 收尾保存
    inst_b.release.set()
    await consumer_b
    assert store.get_session(sid_b).messages[-1]["role"] == "assistant"

    # A 的引擎缓冲未被 B 污染
    assert all(m.get("content") != "B消息" for m in inst_a.mutable_messages)

    # 放行 A
    inst_a.release.set()
    await consumer_a
    assert store.get_session(sid_a).messages[-1]["role"] == "assistant"
    assert all(m.get("content") != "B消息" for m in store.get_session(sid_a).messages)


@pytest.mark.asyncio
async def test_same_session_serial_rejected(workspace, env):
    """同会话已有运行任务时再发消息：被拒绝并提示。"""
    engine, store = env
    store.add_workspace(str(workspace))
    sid = store.create_session(str(workspace)).id

    consumer = asyncio.create_task(collect(chat_event_stream("第一条", sid)))
    inst = await get_inst(engine, 0)
    await inst.started.wait()

    events = await collect(chat_event_stream("第二条", sid))
    assert events[0]["type"] == "error"

    inst.release.set()
    await consumer
    # 第一条正常收尾
    assert store.get_session(sid).messages[-1]["role"] == "assistant"


# --- 收尾回写视图 ---


@pytest.mark.asyncio
async def test_writeback_when_view_unchanged(workspace, env):
    """停留：查看会话未被切换，收尾回写全局视图。"""
    engine, store = env
    store.add_workspace(str(workspace))
    sid = store.create_session(str(workspace)).id

    consumer = asyncio.create_task(collect(chat_event_stream("消息", sid)))
    inst = await get_inst(engine, 0)
    await inst.started.wait()
    inst.release.set()
    await consumer

    assert server.state.engine_session_id == sid
    assert engine.mutable_messages == inst.mutable_messages  # 视图已回写


@pytest.mark.asyncio
async def test_writeback_skipped_when_view_switched_away(workspace, env):
    """切走：run 期间查看会话被切换，收尾不回写全局视图。"""
    engine, store = env
    store.add_workspace(str(workspace))
    sid = store.create_session(str(workspace)).id
    view_before = [{"role": "user", "content": "视图旧内容"}]
    engine.mutable_messages = list(view_before)

    consumer = asyncio.create_task(collect(chat_event_stream("消息", sid)))
    inst = await get_inst(engine, 0)
    await inst.started.wait()
    # 切走查看会话
    server.state.engine_session_id = "other-session"
    inst.release.set()
    await consumer

    # 视图未被回写（保持旧内容）；任务内容已保存到自己的会话
    assert engine.mutable_messages == view_before
    assert store.get_session(sid).messages[-1]["role"] == "assistant"


@pytest.mark.asyncio
async def test_writeback_when_view_switched_back(workspace, env):
    """切走又切回：收尾仍回写全局视图（switch 把 engine_session_id 改回本会话）。"""
    engine, store = env
    store.add_workspace(str(workspace))
    sid = store.create_session(str(workspace)).id

    consumer = asyncio.create_task(collect(chat_event_stream("消息", sid)))
    inst = await get_inst(engine, 0)
    await inst.started.wait()
    server.state.engine_session_id = "other-session"
    server.state.engine_session_id = sid  # 切回
    inst.release.set()
    await consumer

    assert engine.mutable_messages == inst.mutable_messages  # 回写生效


@pytest.mark.asyncio
async def test_auto_create_first_round_writeback(workspace, env):
    """自动建会话首轮回写：注册时 engine_session_id 指向新会话；
    第二轮快照含首轮完整对话，立即持久化不覆盖丢失。"""
    engine, store = env
    store.add_workspace(str(workspace))

    # 首轮：自动建会话
    consumer = asyncio.create_task(collect(chat_event_stream("首轮消息", "")))
    inst0 = await get_inst(engine, 0)
    await inst0.started.wait()
    inst0.release.set()
    events = await consumer
    sid = events[0]["session_id"]
    assert events[0]["type"] == "session_meta"
    # 回写：视图同步任务引擎最终消息
    assert server.state.engine_session_id == sid
    assert engine.mutable_messages == inst0.mutable_messages

    # 第二轮：快照取 DB 前缀（含首轮完整对话），立即持久化不覆盖
    consumer2 = asyncio.create_task(collect(chat_event_stream("第二轮消息", sid)))
    inst1 = await get_inst(engine, 1)
    await inst1.started.wait()
    session = store.get_session(sid)
    assert session.messages == [
        {"role": "user", "content": "首轮消息"},
        {"role": "assistant", "content": "回复内容"},
        {"role": "user", "content": "第二轮消息"},
    ]
    inst1.release.set()
    await consumer2


# --- cwd 隔离 ---


@pytest.mark.asyncio
async def test_task_workspace_contextvar(workspace, env):
    """任务上下文里 workspace_var 被设置为任务所属工作区。"""
    engine, store = env
    store.add_workspace(str(workspace))
    sid = store.create_session(str(workspace)).id

    consumer = asyncio.create_task(collect(chat_event_stream("消息", sid)))
    inst = await get_inst(engine, 0)
    await inst.started.wait()

    # 任务协程内观测到的工作区 == 会话所属工作区
    assert inst.observed_workspace == str(workspace)

    inst.release.set()
    await consumer


def test_effective_root_fallback_without_task_context(workspace):
    """非任务上下文：workspace_var 为空，effective_root 回退全局 project_root。"""
    from server.paths import effective_root, project_root

    assert server.state.workspace_var.get() is None
    assert effective_root() == project_root()


# --- abort 按 session ---


@pytest.mark.asyncio
async def test_abort_by_session(workspace, env):
    """abort 按 session_id 中止指定任务并等待收尾。"""
    engine, store = env
    store.add_workspace(str(workspace))
    sid = store.create_session(str(workspace)).id

    consumer = asyncio.create_task(collect(chat_event_stream("消息", sid)))
    inst = await get_inst(engine, 0)
    await inst.started.wait()

    resp = await abort_query(await make_request({"session_id": sid}))
    assert resp.status_code == 200
    # 任务被中止并收尾：移出注册表、用户消息已保存（无 assistant，被中断）
    assert sid not in server.state.running_runs
    session = store.get_session(sid)
    assert [m["content"] for m in session.messages] == ["消息"]

    # 无任务会话：返回 no running task
    resp2 = await abort_query(await make_request({"session_id": "nonexistent"}))
    assert resp2.status_code == 200
    assert "no running task" in str(resp2.body)


# --- 桥广播与 /api/state ---


@pytest.mark.asyncio
async def test_bridge_broadcast_and_resolve(workspace, env):
    """桥未决表状态查询式：多订阅者可见，resolve 后消失。"""
    engine, store = env
    pb = server.state.permission_bridge

    pending_task = asyncio.create_task(
        pb.request_permission("bash", {"command": "ls"}, "测试", session_id="s1")
    )
    await asyncio.sleep(0)  # 让请求挂起

    pending = pb.get_pending_requests()
    assert len(pending) == 1
    assert pending[0]["session_id"] == "s1"
    # 非消费式：再次读取同一份
    assert pb.get_pending_requests() == pending

    assert pb.resolve(pending[0]["request_id"], "allow") is True
    assert pb.get_pending_requests() == []
    assert await pending_task == "allow"


@pytest.mark.asyncio
async def test_state_returns_running_task_messages(workspace, env):
    """运行任务时 /api/state 返回任务引擎的实时消息。"""
    engine, store = env
    store.add_workspace(str(workspace))
    sid = store.create_session(str(workspace)).id

    consumer = asyncio.create_task(collect(chat_event_stream("消息", sid)))
    inst = await get_inst(engine, 0)
    await inst.started.wait()

    state = await get_state()
    assert state["messages"] == inst.mutable_messages  # 实时消息

    inst.release.set()
    await consumer
