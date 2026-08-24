"""跨工作区并行测试：双任务并行 + 中途切视图的上下文归属、修复点定向单测。

复用 test_background_task 的装配模式：chat 路由的 QueryEngine 替换为
FakeEngine（每任务一个实例），真实 SessionStore。覆盖两类断言：
1. 两个不同工作区的会话任务并行运行，中途切换全局视图工作区后，
   各任务观测到的 workspace_var / effective_root 不随视图变化，
   收尾保存互不串写，中止其一不影响另一个。
2. 子代理定义目录与 PreCompact hooks cwd 两处修复点，
   在任务上下文内取任务自己的工作区，非任务上下文回退全局根。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.requests import Request

import server.state
from server.paths import effective_root, project_root, set_project_root
from server.routers.chat.routes import abort_query, chat_event_stream
from server.permission_bridge import PermissionBridge
from server.question_bridge import QuestionBridge
from session.store import SessionStore


class FakeEngine:
    """假任务引擎：记录任务上下文里的 workspace_var 与 effective_root。"""

    def __init__(self, messages: list | None = None) -> None:
        self.mutable_messages: list = messages or []
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.observed_workspace: str | None = None
        self.observed_effective_root: str | None = None

    async def submitMessage(self, prompt, user_context=None, system_context=None):
        # 任务执行上下文里观测（workspace_var 由任务协程设置）
        self.observed_workspace = server.state.workspace_var.get()
        self.observed_effective_root = effective_root()
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
                cache_read=0,
                cache_creation=0,
                last_prompt_tokens=0,
                last_cache_creation=0,
            ),
            model="test-model",
            total_cost_usd=0.0,
        )


@pytest.fixture
def env(workspace, monkeypatch):
    """装配 server.state：FakeEngine 任务引擎（每任务一个实例）+ 真实存储与桥。"""
    from query.engine import QueryEngineConfig
    from server.routers.chat import routes as chat_routes

    store = SessionStore(db_path=workspace / "sessions.db")
    engine = FakeEngine()
    engine.instances: list[FakeEngine] = []
    monkeypatch.setattr(server.state, "session_store", store)
    monkeypatch.setattr(server.state, "engine", engine)
    monkeypatch.setattr(server.state, "running_runs", {})
    monkeypatch.setattr(server.state, "permission_bridge", PermissionBridge())
    monkeypatch.setattr(server.state, "question_bridge", QuestionBridge())
    monkeypatch.setattr(server.state, "app_state", FakeAppState())
    monkeypatch.setattr(server.state, "engine_session_id", None)

    def fake_query_engine(config, initial_messages=None, session_id=""):
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


# --- 跨工作区并行回归 ---


@pytest.mark.asyncio
async def test_cross_workspace_parallel_with_view_switch(workspace, env):
    """跨工作区双任务并行：中途切视图后各任务上下文归属不变，收尾互不串写。"""
    engine, store = env
    ws_a = workspace / "ws_a"
    ws_b = workspace / "ws_b"
    ws_a.mkdir()
    ws_b.mkdir()
    store.add_workspace(str(ws_a))
    store.add_workspace(str(ws_b))
    sid_a = store.create_session(str(ws_a)).id
    sid_b = store.create_session(str(ws_b)).id

    # 两个工作区的任务先后启动，并行运行
    consumer_a = asyncio.create_task(collect(chat_event_stream("A任务消息", sid_a)))
    inst_a = await get_inst(engine, 0)
    await inst_a.started.wait()

    consumer_b = asyncio.create_task(collect(chat_event_stream("B任务消息", sid_b)))
    inst_b = await get_inst(engine, 1)
    await inst_b.started.wait()

    assert sid_a in server.state.running_runs
    assert sid_b in server.state.running_runs

    # 中途把全局视图切到 B 工作区（模拟用户切换工作区查看）
    set_project_root(str(ws_b))

    # 任务上下文归属不随视图切换变化：A 仍是 ws_a，B 仍是 ws_b
    assert inst_a.observed_workspace == str(ws_a)
    assert inst_b.observed_workspace == str(ws_b)
    assert inst_a.observed_effective_root == str(ws_a)
    assert inst_b.observed_effective_root == str(ws_b)

    # 放行 B：B 收尾保存到自己的会话，A 继续后台运行
    inst_b.release.set()
    await consumer_b
    assert sid_a in server.state.running_runs
    session_b = store.get_session(sid_b)
    assert session_b.messages[-1]["role"] == "assistant"
    assert all(m.get("content") != "A任务消息" for m in session_b.messages)

    # 放行 A：A 收尾保存到自己的会话，无 B 的内容串入
    inst_a.release.set()
    await consumer_a
    session_a = store.get_session(sid_a)
    assert session_a.messages[-1]["role"] == "assistant"
    assert all(m.get("content") != "B任务消息" for m in session_a.messages)


@pytest.mark.asyncio
async def test_abort_one_cross_workspace_task_other_unaffected(workspace, env):
    """中止跨工作区并行任务之一：另一个继续运行并正常收尾。"""
    engine, store = env
    ws_a = workspace / "ws_a"
    ws_b = workspace / "ws_b"
    ws_a.mkdir()
    ws_b.mkdir()
    store.add_workspace(str(ws_a))
    store.add_workspace(str(ws_b))
    sid_a = store.create_session(str(ws_a)).id
    sid_b = store.create_session(str(ws_b)).id

    consumer_a = asyncio.create_task(collect(chat_event_stream("A任务消息", sid_a)))
    inst_a = await get_inst(engine, 0)
    await inst_a.started.wait()

    consumer_b = asyncio.create_task(collect(chat_event_stream("B任务消息", sid_b)))
    inst_b = await get_inst(engine, 1)
    await inst_b.started.wait()

    # 中止 A：按 session 定向，B 不受影响
    resp = await abort_query(await make_request({"session_id": sid_a}))
    assert resp.status_code == 200
    assert sid_a not in server.state.running_runs
    assert sid_b in server.state.running_runs

    # B 正常跑完收尾
    inst_b.release.set()
    await consumer_b
    assert store.get_session(sid_b).messages[-1]["role"] == "assistant"


# --- 修复点定向单测：子代理定义目录 ---


def test_project_agents_dir_follows_task_workspace(workspace):
    """任务上下文内：项目级子代理目录取任务自己的工作区。"""
    from tools.subagent.loader import get_project_agents_dir

    token = server.state.workspace_var.set(str(workspace))
    try:
        assert get_project_agents_dir() == Path(workspace) / ".agent" / "agents"
    finally:
        server.state.workspace_var.reset(token)


def test_project_agents_dir_fallback_to_view_root(workspace):
    """非任务上下文：回退全局 project_root（当前查看的工作区）。"""
    from tools.subagent.loader import get_project_agents_dir

    assert server.state.workspace_var.get() is None
    assert get_project_agents_dir() == Path(project_root()) / ".agent" / "agents"


# --- 修复点定向单测：PreCompact hooks cwd ---


async def _run_compact_with_fake_hooks(monkeypatch) -> list[str]:
    """跑一次 _generate_compact_summary，返回 PreCompact hooks 收到的 cwd 列表。"""
    import startup.hooks
    import startup.setup
    from query.services.compact import auto_compact

    recorded: list[str] = []

    async def fake_hooks(snapshot, trigger, session_id, cwd):
        recorded.append(cwd)
        return ""

    async def fake_stream(**kw):
        yield SimpleNamespace(type="content", content="摘要内容")

    monkeypatch.setattr(startup.hooks, "run_pre_compact_hooks", fake_hooks)
    # 快照非 None 才会走到 hooks 调用
    monkeypatch.setattr(startup.setup, "get_hooks_snapshot", lambda: object())
    monkeypatch.setattr("query.services.api.llm.query_model_with_streaming", fake_stream)

    summary = await auto_compact._generate_compact_summary(
        [{"role": "user", "content": "历史消息"}], "test-model"
    )
    assert summary  # 摘要生成成功（fake LLM 返回）
    return recorded


@pytest.mark.asyncio
async def test_pre_compact_hooks_cwd_follows_task_workspace(workspace, monkeypatch):
    """后台任务自动压缩：PreCompact hooks 的 cwd 取任务自己的工作区。"""
    token = server.state.workspace_var.set(str(workspace))
    try:
        recorded = await _run_compact_with_fake_hooks(monkeypatch)
    finally:
        server.state.workspace_var.reset(token)
    assert recorded == [str(workspace)]


@pytest.mark.asyncio
async def test_pre_compact_hooks_cwd_fallback_to_view_root(workspace, monkeypatch):
    """非任务上下文压缩：hooks cwd 回退全局 project_root（视图工作区）。"""
    assert server.state.workspace_var.get() is None
    recorded = await _run_compact_with_fake_hooks(monkeypatch)
    assert recorded == [project_root()]
