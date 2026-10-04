"""上下文压缩机制改造的回归测试。

覆盖：统一计数口径、阈值公式与窗口查表归一化、触发守卫（冷却/无新内容/
快速再满/熔断）、压缩输入序列化（tool_calls/工具名/图片/旧摘要内联）、
工程折叠层、插入式边界与全量保留、摘要超长降级重试、微压缩重构、
转录导出与可见序号跳过摘要。
"""

from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

import pytest

import query.services.compact.auto_compact as auto_compact
from query.services.compact.auto_compact import (
    CompactResult,
    CompactTracking,
    STATUS_BREAKER,
    STATUS_COMPACTED,
    STATUS_SKIPPED_COOLDOWN,
    STATUS_SKIPPED_NO_NEW,
    STATUS_SKIPPED_RAPID_REFILL,
    _compact_gate,
    auto_compact_if_needed,
    compact_conversation,
    get_auto_compact_threshold,
)
from query.services.compact.engineering import fold_messages
from query.services.compact.prompt import build_compact_prompt
from query.services.compact.micro_compact import (
    MICRO_CANDIDATE_TOOLS,
    micro_compact_messages,
    should_micro_compact,
)
from query.utils.messages import (
    compact_pivot_index,
    get_messages_after_compact_boundary,
    group_rounds,
)
from query.utils.tokens import count_context_tokens


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """隔离压缩相关环境变量，避免相互影响。"""
    for var in (
        "DISABLE_COMPACT", "DISABLE_AUTO_COMPACT",
        "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE",
    ):
        monkeypatch.delenv(var, raising=False)


# ---------------------------------------------------------------------------
# 统一计数口径
# ---------------------------------------------------------------------------


def test_count_context_tokens_usage_baseline_plus_increment():
    msgs = [
        {"role": "user", "content": "Q" * 300},
        {"role": "assistant", "content": "A", "_context_usage": 1000},
        {"role": "tool", "tool_call_id": "c1", "content": "T" * 300},
    ]
    # 基线 1000 + 其后消息按字符÷3
    total = count_context_tokens(msgs)
    tail_chars = len(json.dumps(msgs[2], ensure_ascii=False))
    assert total == 1000 + tail_chars // 3


def test_count_context_tokens_fallback_no_usage():
    msgs = [{"role": "user", "content": "x" * 600}]
    chars = len(json.dumps(msgs[0], ensure_ascii=False))
    assert count_context_tokens(msgs) == chars // 3


def test_count_context_tokens_slices_active_window():
    """边界前的历史与旧 usage 基线不计入。"""
    boundary = {"role": "system", "content": "[Compact Boundary — auto — pre-compact tokens: 999999]"}
    old = {"role": "assistant", "content": "old", "_context_usage": 500000}
    msgs = [old, boundary, {"role": "user", "content": "new" * 30}]
    total = count_context_tokens(msgs)
    assert total < 500000  # 旧基线不被沿用


# ---------------------------------------------------------------------------
# 阈值公式与窗口查表
# ---------------------------------------------------------------------------


def test_threshold_formula_includes_output_reserve():
    # gpt-4o 内置：128000 窗口、16384 输出 → 128000-16384-13000
    assert get_auto_compact_threshold("gpt-4o") == 128000 - 16384 - 13000


def test_threshold_respects_pct_override_env(monkeypatch):
    monkeypatch.setenv("CLAUDE_AUTOCOMPACT_PCT_OVERRIDE", "50")
    # 有效窗口 128000-16384=111616 的 50%，且不高于新公式阈值
    assert get_auto_compact_threshold("gpt-4o") == int((128000 - 16384) * 0.5)


def test_model_window_normalization_and_source():
    from startup.model.config import get_model_window_info

    # 日期后缀归一化命中内置表
    window, max_output, source = get_model_window_info("gpt-4o-2026-05-13")
    assert (window, max_output, source) == (128000, 16384, "internal")
    # 未命中回退并标注来源
    w2, _m2, s2 = get_model_window_info("entirely-unknown-model-777")
    assert (w2, s2) == (200000, "fallback")


def test_model_window_from_provider_with_max_output(monkeypatch):
    from startup.model.config import get_model_window_info

    cfg = SimpleNamespace(llm_providers=[
        {"models": [{"model_id": "big-window-model", "context_window": 1000000,
                    "max_output_tokens": 65536}]},
    ])
    import startup.config as sc
    monkeypatch.setattr(sc, "get_global_config", lambda: cfg)
    window, max_output, source = get_model_window_info("big-window-model")
    assert (window, max_output, source) == (1000000, 65536, "provider")


# ---------------------------------------------------------------------------
# 触发守卫
# ---------------------------------------------------------------------------


def _big_msgs(n_msgs: int = 2, chars: int = 3000) -> list[dict]:
    out: list[dict] = []
    for i in range(n_msgs):
        out.append({"role": "user", "content": "u" * chars})
        out.append({"role": "assistant", "content": "a" * chars, "_ts": time.time() * 1000})
    return out


def test_gate_cooldown_skips(monkeypatch):
    monkeypatch.setenv("CLAUDE_AUTOCOMPACT_PCT_OVERRIDE", "1")
    tracking = CompactTracking(last_compact_time=time.time())
    result = _compact_gate(_big_msgs(), "gpt-4o", tracking)
    assert result is not None and result.status == STATUS_SKIPPED_COOLDOWN


def test_gate_breaker_skips():
    tracking = CompactTracking(consecutive_failures=3)
    result = _compact_gate(_big_msgs(), "gpt-4o", tracking)
    assert result is not None and result.status == STATUS_BREAKER


def test_gate_no_new_content_skips(monkeypatch):
    """跨回合仍生效：last_compact_count 快照 + 无新增轮组/增幅不足。"""
    monkeypatch.setenv("CLAUDE_AUTOCOMPACT_PCT_OVERRIDE", "1")
    msgs = _big_msgs()
    tokens = count_context_tokens(msgs)
    tracking = CompactTracking(
        last_compact_time=0, last_compact_count=tokens,
        last_compact_assistant_rounds=2,
    )
    result = _compact_gate(msgs, "gpt-4o", tracking)
    assert result is not None and result.status == STATUS_SKIPPED_NO_NEW


def test_gate_rapid_refill_skips(monkeypatch):
    monkeypatch.setenv("CLAUDE_AUTOCOMPACT_PCT_OVERRIDE", "1")
    tracking = CompactTracking(
        last_compact_time=0,
        last_compact_count=1, last_compact_assistant_rounds=0,
        refill_within_rounds=3,
    )
    result = _compact_gate(_big_msgs(chars=9000), "gpt-4o", tracking)
    assert result is not None and result.status == STATUS_SKIPPED_RAPID_REFILL


def test_success_updates_session_level_snapshot(monkeypatch):
    """压缩成功后刷新冷却基线/计数快照，rapid-refill 在成功路径递增。"""
    monkeypatch.setenv("CLAUDE_AUTOCOMPACT_PCT_OVERRIDE", "1")
    msgs = _big_msgs()
    inserted = [*msgs,
                {"role": "system", "content": "[Compact Boundary — auto — pre-compact tokens: 1]"},
                {"role": "user", "content": "S", "_compact_summary": True}]

    async def fake_compact(messages, model, keep_groups=1, transcript_path=None,
                           custom_instructions=None):
        return inserted

    monkeypatch.setattr(auto_compact, "compact_conversation", fake_compact)
    tracking = CompactTracking(last_compact_count=10, last_compact_assistant_rounds=1)
    out, result = asyncio.run(auto_compact_if_needed(msgs, "gpt-4o", tracking))
    assert result.status == STATUS_COMPACTED
    assert out == inserted
    assert tracking.last_compact_time == pytest.approx(time.time(), abs=5)
    assert tracking.last_compact_count == result.tokens_after
    # 距上次压缩新增轮组 0 < 3 → 记一次快速再满
    assert tracking.refill_within_rounds == 1


# ---------------------------------------------------------------------------
# 压缩输入序列化
# ---------------------------------------------------------------------------


def test_serialize_includes_tool_calls_and_names():
    msgs = [
        {"role": "user", "content": "fix it"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "Edit", "arguments": json.dumps({"file_path": "a.py"})}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "ok"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c2", "type": "function",
             "function": {"name": "Read", "arguments": json.dumps({"file_path": "img"})}}]},
        {"role": "tool", "tool_call_id": "c2",
         "content": [{"type": "text", "text": "body"}, {"type": "image", "data": "AAAA"}]},
    ]
    prompt = build_compact_prompt(msgs)
    assert "[assistant called Edit]" in prompt
    assert "a.py" in prompt
    assert "[tool result from Edit]: ok" in prompt
    assert "[image]" in prompt
    assert '"data"' not in prompt  # base64 附件剥离


def test_serialize_truncates_long_args():
    big_args = json.dumps({"x": "A" * 5000})
    msgs = [{"role": "assistant", "content": "", "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "Bash", "arguments": big_args}}]}]
    prompt = build_compact_prompt(msgs)
    assert "(truncated)" in prompt


def test_prior_summary_inlined_not_resummarized():
    msgs = [
        {"role": "user", "content": "earlier work done", "_compact_summary": True},
        {"role": "user", "content": "new question"},
    ]
    prompt = build_compact_prompt(msgs)
    assert "Summary of the conversation so far" in prompt
    # 旧摘要不进 <conversation> 的逐条序列化
    conversation_part = prompt.split("<conversation>")[1]
    assert "[user]: earlier work done" not in conversation_part


# ---------------------------------------------------------------------------
# 工程折叠层
# ---------------------------------------------------------------------------


def test_fold_messages_structure():
    msgs = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "think", "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "Read", "arguments": "{\"p\":1}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "HUGE" * 100},
        {"role": "user", "content": "old summary", "_compact_summary": True},
    ]
    text, est = fold_messages(msgs)
    assert "<previous_user_message>hello</previous_user_message>" in text
    assert 'previous_tool_call name="Read"' in text
    assert "<omitted />" in text
    assert "HUGE" not in text  # 工具结果一律省略
    assert "<cb_summary>" in text and "</cb_summary>" in text
    assert est == len(text) // 3


def test_compact_skips_llm_when_fold_small(monkeypatch):
    """折叠估算 ≤ 窗口×15%：直接采纳，不调 LLM。"""
    calls = {"n": 0}

    async def spy(*a, **kw):
        calls["n"] += 1
        raise AssertionError("不应调用 LLM")

    monkeypatch.setattr(auto_compact, "_generate_compact_summary", spy)
    msgs = _big_msgs(n_msgs=2, chars=10)
    out = asyncio.run(compact_conversation(msgs, "gpt-4o", keep_groups=1))
    assert calls["n"] == 0
    assert any(m.get("_compact_summary") for m in out)


def test_compact_calls_llm_with_folded_input_when_large(monkeypatch):
    """>15% 分支：调一次 LLM 且以折叠文本为输入。"""
    seen: dict = {}

    async def spy(messages, model, pre_folded_text=None, custom_instructions=None):
        seen["pre_folded_text"] = pre_folded_text
        seen["custom"] = custom_instructions
        return "<summary>from llm</summary>"

    monkeypatch.setattr(auto_compact, "_generate_compact_summary", spy)
    # 窗口打小：15% 门控极易越过
    monkeypatch.setattr("startup.model.config.get_effective_context_window", lambda m: 1000)
    msgs = _big_msgs(n_msgs=2, chars=6000)
    out = asyncio.run(compact_conversation(msgs, "gpt-4o", keep_groups=1))
    assert seen.get("pre_folded_text"), "应以工程折叠文本为输入"
    assert "<previous_user_message>" in seen["pre_folded_text"]
    assert "from llm" in out[3]["content"] if len(out) > 3 else True


# ---------------------------------------------------------------------------
# 插入式边界与全量保留
# ---------------------------------------------------------------------------


def test_pivot_grouping_keeps_tool_pairs():
    msgs = [
        {"role": "user", "content": "old question"},
        {"role": "assistant", "content": "old answer"},
        {"role": "user", "content": "new question"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "Read", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "result"},
    ]
    pivot = compact_pivot_index(msgs, keep_groups=1)
    # 保留组从 "new question"（组首 user）起
    assert msgs[pivot]["content"] == "new question"
    out = asyncio.run(compact_conversation(msgs, "gpt-4o", keep_groups=1))
    assert len(out) == len(msgs) + 2
    active = get_messages_after_compact_boundary(out)
    # 活跃窗口 = 边界+摘要+保留组，tool_call/tool 成对完整
    assert active[0]["content"].startswith("[Compact Boundary")
    assert active[1]["_compact_summary"] is True
    kept = active[2:]
    assert [m["role"] for m in kept] == ["user", "assistant", "tool"]


def test_keep_zero_compacts_everything():
    msgs = _big_msgs(n_msgs=3)
    out = asyncio.run(compact_conversation(msgs, "gpt-4o", keep_groups=0))
    active = get_messages_after_compact_boundary(out)
    assert len(active) == 2  # 仅边界+摘要
    assert len(out) == len(msgs) + 2  # 全量保留


def test_second_compaction_inserts_new_boundary_only():
    msgs = _big_msgs(n_msgs=2, chars=4000)
    first = asyncio.run(compact_conversation(msgs, "gpt-4o", keep_groups=1))
    first = [*first, {"role": "user", "content": "n" * 4000},
             {"role": "assistant", "content": "m" * 4000, "_ts": time.time() * 1000}]
    second = asyncio.run(compact_conversation(first, "gpt-4o", keep_groups=1))
    boundaries = [m for m in second if m.get("content", "").startswith("[Compact Boundary")]
    assert len(boundaries) == 2
    # 全量保留：第一条边界仍在（历史不断增长，不删除）
    idx_last = max(i for i, m in enumerate(second)
                   if m.get("content", "").startswith("[Compact Boundary"))
    assert second[idx_last - 1 : idx_last + 1][0] is not None
    active = get_messages_after_compact_boundary(second)
    assert active[0]["content"].startswith("[Compact Boundary")
    # 第二次待压缩范围内不含第二个边界之后的消息
    assert len(active) < len(second)


# ---------------------------------------------------------------------------
# 摘要超长降级重试
# ---------------------------------------------------------------------------


def test_summary_retry_drops_oldest_rounds(monkeypatch):
    attempts = {"n": 0}

    async def fake_stream(messages=None, model=None, **kw):
        attempts["n"] += 1
        if attempts["n"] == 1:
            yield SimpleNamespace(type="error",
                                  content="prompt is too long: 150000 tokens > 100000 maximum",
                                  error=None)
        else:
            yield SimpleNamespace(type="content", content="<summary>ok</summary>", error=None)

    import query.services.api.llm as llm_mod
    monkeypatch.setattr(llm_mod, "query_model_with_streaming", fake_stream)
    msgs = _big_msgs(n_msgs=6, chars=20000)
    out = asyncio.run(auto_compact._generate_compact_summary(msgs, "gpt-4o"))
    assert attempts["n"] == 2
    assert "[earlier conversation truncated for compaction retry]" in out
    assert "ok" in out


def test_summary_retry_exhausted_raises(monkeypatch):
    async def always_too_long(messages=None, model=None, **kw):
        yield SimpleNamespace(type="error",
                              content="prompt is too long: 999999 tokens > 1000 maximum",
                              error=None)

    import query.services.api.llm as llm_mod
    monkeypatch.setattr(llm_mod, "query_model_with_streaming", always_too_long)
    msgs = [{"role": "user", "content": "only one round"}]
    with pytest.raises(RuntimeError):
        asyncio.run(auto_compact._generate_compact_summary(msgs, "gpt-4o"))


# ---------------------------------------------------------------------------
# 微压缩重构
# ---------------------------------------------------------------------------


def _rounds(n: int, tool: str = "Read", result_chars: int = 3000) -> list[dict]:
    msgs: list[dict] = []
    for i in range(n):
        tc_id = f"c{i}"
        msgs.append({"role": "user", "content": "q"})
        msgs.append({
            "role": "assistant", "content": "", "_ts": time.time() * 1000,
            "tool_calls": [{"id": tc_id, "type": "function",
                            "function": {"name": tool, "arguments": "{}"}}],
        })
        msgs.append({"role": "tool", "tool_call_id": tc_id, "content": "x" * result_chars})
    return msgs


def test_micro_compact_whitelist_and_keep5():
    msgs = _rounds(8)
    out = micro_compact_messages(msgs)
    tool_msgs = [m for m in out if m.get("role") == "tool"]
    cleared = [m for m in tool_msgs if m["content"].startswith("[Old tool result content cleared")]
    assert len(cleared) == 3  # 8 组保留最近 5 组
    assert cleared[0]["content"] == "[Old tool result content cleared — Read]"


def test_micro_compact_non_whitelist_kept():
    msgs = _rounds(8, tool="AskUserQuestion")
    out = micro_compact_messages(msgs)
    assert all(not m["content"].startswith("[Old tool result")
               for m in out if m.get("role") == "tool")


def test_micro_compact_skips_small_savings():
    msgs = _rounds(8, result_chars=60)  # 每组省 ~20 token
    out = micro_compact_messages(msgs, min_savings=256)
    assert out is msgs  # 未达门槛不改写


def test_micro_compact_idle_trigger():
    old_ts = (time.time() - 61 * 60) * 1000
    msgs = _rounds(1)
    msgs[-1]["_ts"] = old_ts
    msgs.append({"role": "assistant", "content": "a", "_ts": old_ts})
    assert should_micro_compact(msgs) is True
    fresh = _rounds(2)
    assert should_micro_compact(fresh) is False


def test_micro_compact_waterline_trigger(monkeypatch):
    monkeypatch.setenv("CLAUDE_AUTOCOMPACT_PCT_OVERRIDE", "1")
    msgs = _rounds(3, result_chars=5000)
    assert should_micro_compact(msgs, "gpt-4o") is True


def test_micro_compact_only_touches_active_window():
    boundary = {"role": "system", "content": "[Compact Boundary — auto — pre-compact tokens: 1]"}
    old_active = _rounds(8)
    msgs = old_active + [boundary] + _rounds(1)
    out = micro_compact_messages(msgs)
    # 边界前的旧消息原样（对象同一），不被改写
    for i, m in enumerate(msgs[:len(old_active)]):
        assert out[i] is m


# ---------------------------------------------------------------------------
# 转录导出与可见序号
# ---------------------------------------------------------------------------


def test_export_transcript_writes_jsonl(tmp_path):
    from session.store import SessionStore
    store = SessionStore(db_path=tmp_path / "sessions.db")
    session = store.create_session(str(tmp_path), title="t")
    out = store.export_transcript(session.id, [{"role": "user", "content": "a"},
                                                {"role": "assistant", "content": "b"}])
    with open(out, encoding="utf-8") as f:
        lines = [json.loads(x) for x in f if x.strip()]
    assert len(lines) == 2 and lines[1]["role"] == "assistant"


def test_visible_user_indexes_skip_compact_summary():
    from server.routers.chat.routes import _visible_user_indexes
    msgs = [
        {"role": "user", "content": "real question"},
        {"role": "user", "content": "summary...", "_compact_summary": True},
        {"role": "user", "content": "<system-reminder>skill</system-reminder>"},
        {"role": "user", "content": "next"},
    ]
    assert _visible_user_indexes(msgs) == [0, 3]


def test_serialize_event_passes_compact_info():
    """compact_* 事件的载荷经 serialize_event 透传到 SSE。"""
    from server.routers.chat.routes import serialize_event
    from query.services.api.llm import StreamEvent

    ev = StreamEvent(
        type="compact_completed",
        compact_info={"status": "compacted", "tokens_before": 100,
                      "tokens_after": 10, "reason": ""},
    )
    out = serialize_event(ev)
    assert out["event_type"] == "compact_completed"
    assert out["compact_info"]["tokens_before"] == 100
    assert out["compact_info"]["tokens_after"] == 10
    # 非压缩事件不带 compact_info
    plain = serialize_event(StreamEvent(type="content", content="hi"))
    assert "compact_info" not in plain


# ---------------------------------------------------------------------------
# 长对话集成模拟（40 万级 token，百万窗口）
# ---------------------------------------------------------------------------


def test_long_conversation_integration(monkeypatch, tmp_path):
    """真实 usage 基线下：远低于窗口不压缩；到阈值压一次；
    冷却/无新内容守卫抑制连压；压缩后活跃窗口从边界起、全量保留、
    续写消息带转录路径；下一次请求的 usage 回落到保留规模。"""
    import os
    from query.deps import QueryDeps
    from query.engine import QueryEngine, build_engine_config
    from query.services.api.llm import StreamEvent
    from query.services.compact.micro_compact import micro_compact_messages
    from query.utils.tokens import estimate_tokens_for_messages

    # 窗口固定 100 万；摘要 LLM 打桩避免真实网络
    monkeypatch.setattr(
        "startup.model.config.get_model_window_info",
        lambda m: (1_000_000, 32768, "internal"),
    )

    async def stub_summary(messages, model, pre_folded_text=None, custom_instructions=None):
        return "<summary>stubbed long-range summary</summary>"

    monkeypatch.setattr(auto_compact, "_generate_compact_summary", stub_summary)

    # 转录路径指向临时 home（断言续写消息里的逃生门路径）
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))

    async def fake_call_model(messages=None, tools=None, model=None, **kw):
        # usage 按发送内容估算，模拟真实逐轮增长的 prompt_tokens
        real_input = estimate_tokens_for_messages(list(messages)) * 2  # 中英混合口径
        yield StreamEvent(type="content", content="已完成一步")
        yield StreamEvent(
            type="usage",
            usage={
                "prompt_tokens": real_input, "completion_tokens": 50,
                "total_tokens": real_input + 50, "total_input_tokens": real_input,
            },
        )
        yield StreamEvent(type="done", finish_reason="stop")

    deps = QueryDeps(
        call_model=fake_call_model,
        microcompact=micro_compact_messages,
        autocompact=auto_compact_if_needed,
    )
    config = build_engine_config(model="test-big-window", max_tokens=1024, deps=deps)
    engine = QueryEngine(config)

    # 阈值 = 1000000-21000-13000 = 966000；每轮注入 ~48k 字符（真实口径约 24k/轮）
    chunk = "数据" * 24000  # 48000 字符
    rounds_total = 42

    async def drive():
        for i in range(rounds_total):
            async for _ in engine.submitMessage(f"第{i}轮：" + chunk):
                pass

    asyncio.run(drive())

    boundaries = [m for m in engine.mutable_messages
                  if m.get("content", "").startswith("[Compact Boundary")]
    summaries = [m for m in engine.mutable_messages if m.get("_compact_summary")]

    # 1) 40 万级（半窗口以下）阶段不压缩：前 17 轮约 ~40 万，全程至多压 1 次
    assert len(boundaries) == 1, f"期望恰好一次压缩，实际 {len(boundaries)}"
    # 2) 全量保留：压缩后引擎消息数 = 原始 2N + 插入 2
    assert len(engine.mutable_messages) == rounds_total * 2 + 2
    # 3) 活跃窗口从边界起：末尾边界之后只有摘要+压缩后新增轮次
    from query.utils.messages import get_messages_after_compact_boundary
    active = get_messages_after_compact_boundary(engine.mutable_messages)
    assert active[0] is boundaries[0]
    assert active[1].get("_compact_summary") is True
    assert len(active) < len(engine.mutable_messages)
    # 4) 续写消息含转录逃生门路径（home 重定向到 tmp）
    assert ".agent" in summaries[0]["content"] and "transcript" in summaries[0]["content"]
    # 5) 压缩后下一次请求的 usage 回落到保留规模（远小于压缩前基线）
    last_usage = [m.get("_context_usage") for m in engine.mutable_messages
                  if isinstance(m.get("_context_usage"), int)]
    assert last_usage[-1] < 300_000, f"压缩后请求未回落：{last_usage[-1]}"
