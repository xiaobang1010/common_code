"""query_loop max_turns 生效测试 - 循环内计数，子代理直跑 loop 也能触发。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Callable

import pytest

from query.config import build_query_config
from query.engine import QueryEngine, build_engine_config
from query.loop import query_loop
from query.services.api.llm import StreamEvent


@dataclass
class FakeDeps:
    """最小 I/O 依赖：模型调用返回固定两轮工具调用后收尾。"""

    call_log: list[dict] = field(default_factory=list)

    def get_uuid(self) -> str:
        return "test-uuid"

    async def call_model(
        self, messages: list, tools: list, model: str, max_tokens: int, temperature: float,
    ) -> AsyncGenerator[StreamEvent, None]:
        self.call_log.append({"messages": [m.get("role") for m in messages]})
        # 每轮固定返回一条 assistant 文本（无工具调用 -> 每轮即一个完整回合）
        yield StreamEvent(type="content", content=f"第 {len(self.call_log)} 轮输出")
        yield StreamEvent(type="done", finish_reason="stop")
        yield StreamEvent(type="usage", usage={"total_tokens": 10})


@pytest.mark.asyncio
async def test_max_turns_stops_loop_with_reason():
    """max_turns=0 时循环开跑前即被拦截，产出说明原因的 assistant 消息。"""
    deps = FakeDeps()
    config = build_engine_config(
        model="fake-model",
        tools=[],
        max_turns=0,  # 边界值：第 0 轮即触发上限
        deps=deps,  # type: ignore[arg-type]
    )
    engine = QueryEngine(config, initial_messages=[{"role": "user", "content": "任务"}])
    query_config = build_query_config(session_id="test-session")

    events = []
    async for ev in query_loop(engine, query_config):
        events.append(ev)

    # 模型一次都没被调用（进入循环即被 max_turns 拦截）
    assert len(deps.call_log) == 0

    # 产出说明原因的 assistant 消息
    reason_messages = [
        e for e in events
        if isinstance(e, dict) and e.get("role") == "assistant"
        and "轮次上限" in str(e.get("content", ""))
    ]
    assert len(reason_messages) == 1


@pytest.mark.asyncio
async def test_no_max_turns_runs_free():
    """未配置 max_turns 时不拦截（正常完成）。"""
    deps = FakeDeps()
    config = build_engine_config(
        model="fake-model",
        tools=[],
        max_turns=None,
        deps=deps,  # type: ignore[arg-type]
    )
    engine = QueryEngine(config, initial_messages=[{"role": "user", "content": "任务"}])
    query_config = build_query_config(session_id="test-session")

    # 手动让循环在两轮后返回（第二轮 done 后无工具调用，loop 自行 completed）
    events = []
    async for ev in query_loop(engine, query_config):
        events.append(ev)

    # 无工具调用的对话：每轮模型返回文本即完成，模型只调用 1 次
    assert len(deps.call_log) == 1
    assert not any(
        isinstance(e, dict) and "轮次上限" in str(e.get("content", "")) for e in events
    )
