"""易变上下文注入落点测试。

覆盖 `insert_message_before_last_user` / `inject_context_before_last_user`
的三分支落点规则（首轮 / 工具续写轮 / 无 user 消息），以及经
query_loop 的集成用例：技能清单不再出现在消息列表头部。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import AsyncGenerator

import pytest

from query.config import build_query_config
from query.engine import QueryEngine, build_engine_config
from query.loop import query_loop
from query.services.api.llm import StreamEvent
from query.utils.api import (
    inject_context_before_last_user,
    insert_message_before_last_user,
)

_MARKER = {"role": "user", "content": "<system-reminder>MARKER</system-reminder>"}


def _user(text: str) -> dict:
    return {"role": "user", "content": text}


def _assistant_calls() -> dict:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"id": "t1", "function": {"name": "Bash", "arguments": "{}"}}],
    }


def _tool_result() -> dict:
    return {"role": "tool", "tool_call_id": "t1", "content": "ok"}


# ---- 三分支落点规则 ----

def test_first_turn_inserts_before_last_user():
    """首轮：无工具流量时，插到最后一条 user 消息之前，总数 +1。"""
    msgs = [_user("问题一"), {"role": "assistant", "content": "回答"}, _user("问题二")]
    out = insert_message_before_last_user(msgs, dict(_MARKER))
    assert len(out) == len(msgs) + 1
    assert out[-1] == msgs[-1]  # 用户问题仍在最末
    assert out[-2]["content"] == _MARKER["content"]


def test_tool_continuation_appends_to_tail():
    """工具续写轮：末尾是本轮工具流量时，追加到列表末尾。"""
    msgs = [_user("任务"), _assistant_calls(), _tool_result()]
    out = insert_message_before_last_user(msgs, dict(_MARKER))
    assert out[-1]["content"] == _MARKER["content"]
    # 本轮工具流量保持连续，没有被 reminder 拆散
    assert [m["role"] for m in out[:-1]] == ["user", "assistant", "tool"]


def test_assistant_with_tool_calls_after_user_triggers_tail():
    """最后 user 之后是带 tool_calls 的 assistant（结果未回），同样追加末尾。"""
    msgs = [_user("任务"), _assistant_calls()]
    out = insert_message_before_last_user(msgs, dict(_MARKER))
    assert out[-1]["content"] == _MARKER["content"]


def test_no_user_message_falls_back_to_append():
    """无 user 消息时兜底追加末尾。"""
    msgs = [{"role": "system", "content": "s"}]
    out = insert_message_before_last_user(msgs, dict(_MARKER))
    assert out[-1]["content"] == _MARKER["content"]


def test_empty_list():
    """空消息列表：结果即注入消息本身。"""
    assert insert_message_before_last_user([], dict(_MARKER)) == [_MARKER]


# ---- inject_context_before_last_user 包装行为 ----

def test_context_dict_wrapped_as_system_reminder():
    """字典上下文转 # key 分段并包成 system-reminder，落到最后 user 之前。"""
    msgs = [_user("你好")]
    out = inject_context_before_last_user(msgs, {"相关记忆": "- 条目A"})
    assert len(out) == 2
    assert out[0]["role"] == "user"
    assert "<system-reminder>" in out[0]["content"]
    assert "# 相关记忆" in out[0]["content"]
    assert "may or may not be relevant" in out[0]["content"]
    assert out[1] == msgs[0]


def test_empty_context_passthrough():
    """None / 空字典原样返回。"""
    msgs = [_user("hi")]
    assert inject_context_before_last_user(msgs, None) is msgs
    assert inject_context_before_last_user(msgs, {}) is msgs


# ---- query_loop 集成：技能清单不再进头部 ----

@dataclass
class PlacementDeps:
    """记录每次模型调用收到的消息序列，返回固定文本收尾。"""

    calls: list[list[dict]] = field(default_factory=list)

    def get_uuid(self) -> str:
        return "test-uuid"

    async def call_model(
        self, messages: list, tools: list, model: str, max_tokens: int, temperature: float,
    ) -> AsyncGenerator[StreamEvent, None]:
        self.calls.append([dict(m) for m in messages])
        yield StreamEvent(type="content", content="收到")
        yield StreamEvent(type="done", finish_reason="stop")


@pytest.mark.asyncio
async def test_skill_listing_not_prepended_in_loop(monkeypatch):
    """经 query_loop 注入的技能清单落在用户消息之前，而非消息列表头部。"""
    # 让技能清单路径必然触发：有可调用 skill 且清单附件非空
    import tools.skills.bundled as bundled
    import tools.skills.listing as listing

    monkeypatch.setattr(bundled, "get_model_invocable_skills", lambda: [object()])
    monkeypatch.setattr(
        listing, "get_skill_listing_attachment",
        lambda skills, sent, cw: {"role": "user", "content": "<system-reminder>SKILLS</system-reminder>"},
    )

    deps = PlacementDeps()
    config = build_engine_config(model="fake-model", tools=[], max_turns=5, deps=deps)  # type: ignore[arg-type]
    engine = QueryEngine(config, initial_messages=[_user("你好")])

    async for _ in query_loop(engine, build_query_config(session_id="placement-test")):
        pass

    assert len(deps.calls) == 1
    sent = deps.calls[0]
    # 头部仍是 system 段，清单不在最前
    assert sent[0]["role"] == "system"
    marker_idx = next(i for i, m in enumerate(sent) if "SKILLS" in str(m.get("content", "")))
    user_idx = next(i for i, m in enumerate(sent) if m.get("content") == "你好")
    assert marker_idx == user_idx - 1  # 紧贴用户问题之前
