"""推理等级请求链路测试。

覆盖：
- 映射求值器：模板形态替换、按等级形态取值、非法输入返回 None
- 深合并：嵌套合并与 null 删键
- resolve_reasoning_patch 经 get_model_config 单源取配置
- loop 注入：OpenAI 兼容并入 extra_body、Anthropic 并入顶层 kwargs、未选等级不注入
- Anthropic _merge_kwargs 黑名单不含推理相关键（断言 2.4 现状）
- 路由 _resolve_reasoning_level 合法性校验
"""

from __future__ import annotations

import pytest

from query.utils.reasoning import (
    deep_merge_patch,
    parse_params_map,
    resolve_reasoning_params,
    resolve_reasoning_patch,
    validate_params_map,
)
from startup.model.config import ModelConfig


# ---------------------------------------------------------------------------
# 求值器纯函数
# ---------------------------------------------------------------------------


class TestResolveParams:
    def test_template_form(self):
        patch = resolve_reasoning_params(
            '{"reasoning_effort": "{reasoningLevel}"}', ["low", "medium"], "medium"
        )
        assert patch == {"reasoning_effort": "medium"}

    def test_template_nested_placeholder(self):
        patch = resolve_reasoning_params(
            '{"a": {"b": "x-{reasoningLevel}"}}', ["high"], "high"
        )
        assert patch == {"a": {"b": "x-high"}}

    def test_per_level_form(self):
        raw = (
            '{"disabled": {"thinking": {"type": "disabled"}},'
            ' "low": {"thinking": {"type": "enabled"}, "reasoning_effort": "low"}}'
        )
        assert resolve_reasoning_params(raw, ["disabled", "low"], "disabled") == {
            "thinking": {"type": "disabled"}
        }
        assert resolve_reasoning_params(raw, ["disabled", "low"], "low") == {
            "thinking": {"type": "enabled"}, "reasoning_effort": "low",
        }

    def test_no_level_no_patch(self):
        assert resolve_reasoning_params('{"a": 1}', ["low"], "") is None

    def test_level_not_in_list_template(self):
        assert resolve_reasoning_params('{"a": 1}', ["low"], "nope") is None

    def test_bad_map_returns_none(self):
        assert resolve_reasoning_params("[1]", ["low"], "low") is None

    def test_resolve_returns_copy(self):
        raw = '{"x": {"deep": "a"}}'
        p1 = resolve_reasoning_params(raw, ["x"], "x")
        p1["deep"] = "mutated"
        p2 = resolve_reasoning_params(raw, ["x"], "x")
        assert p2["deep"] == "a"


class TestDeepMerge:
    def test_nested_merge_and_null_delete(self):
        base = {"a": 1, "b": {"c": 2, "d": 3}}
        deep_merge_patch(base, {"b": {"c": None, "e": 9}, "f": None})
        assert base == {"a": 1, "b": {"d": 3, "e": 9}}

    def test_conflict_path_overwrites(self):
        base = {"thinking": {"type": "enabled"}}
        deep_merge_patch(base, {"thinking": {"type": "disabled"}})
        assert base["thinking"]["type"] == "disabled"


# ---------------------------------------------------------------------------
# 单源取配置
# ---------------------------------------------------------------------------


def test_resolve_reasoning_patch_uses_get_model_config(monkeypatch):
    fake = ModelConfig(
        name="m", context_window=1, max_output_tokens=1,
        reasoning_levels=("low", "medium"),
        reasoning_params_map='{"reasoning_effort": "{reasoningLevel}"}',
    )
    import startup.model.config as model_config_module
    monkeypatch.setattr(model_config_module, "get_model_config", lambda m: fake)
    assert resolve_reasoning_patch("m", "medium") == {"reasoning_effort": "medium"}
    assert resolve_reasoning_patch("m", "") is None
    assert resolve_reasoning_patch("m", "nope") is None


# ---------------------------------------------------------------------------
# loop 注入
# ---------------------------------------------------------------------------


def _make_loop_harness(monkeypatch, captured: dict, api_format: str, patch):
    """构造驱动 query_loop 的最小环境，捕获 call_model 实发 kwargs。"""
    from dataclasses import dataclass, field as dc_field

    from query.config import build_query_config
    from query.engine import QueryEngine, build_engine_config
    from query.loop import query_loop
    from query.services.api.llm import StreamEvent
    import query.loop as loop_module

    @dataclass
    class FakeDeps:
        calls: int = 0

        def get_uuid(self) -> str:
            return "u"

        async def call_model(self, **kwargs):
            self.calls += 1
            captured.update(kwargs)
            yield StreamEvent(type="content", content="完成")
            yield StreamEvent(type="done", finish_reason="stop")
            yield StreamEvent(type="usage", usage={"total_tokens": 10})

    monkeypatch.setattr(loop_module, "resolve_reasoning_patch", lambda m, lv: patch)
    import query.services.api.client as client_module
    monkeypatch.setattr(client_module, "get_active_api_format", lambda: api_format)
    return FakeDeps(), build_query_config, QueryEngine, build_engine_config, query_loop


@pytest.mark.asyncio
async def test_loop_injects_extra_body_for_openai(monkeypatch):
    captured: dict = {}
    deps, bqc, QE, bec, ql = _make_loop_harness(
        monkeypatch, captured, "openai", {"reasoning_effort": "xhigh"}
    )
    config = bec(model="fake", tools=[], reasoning_level="xhigh", deps=deps)
    engine = QE(config, initial_messages=[{"role": "user", "content": "t"}])
    _ = [ev async for ev in ql(engine, bqc(session_id="s"))]
    assert captured["extra_body"] == {"reasoning_effort": "xhigh"}


@pytest.mark.asyncio
async def test_loop_injects_top_level_for_anthropic(monkeypatch):
    captured: dict = {}
    deps, bqc, QE, bec, ql = _make_loop_harness(
        monkeypatch, captured, "anthropic",
        {"thinking": {"type": "enabled"}, "reasoning_effort": "low"},
    )
    config = bec(model="fake", tools=[], reasoning_level="low", deps=deps)
    engine = QE(config, initial_messages=[{"role": "user", "content": "t"}])
    _ = [ev async for ev in ql(engine, bqc(session_id="s"))]
    assert captured["thinking"] == {"type": "enabled"}
    assert captured["reasoning_effort"] == "low"
    assert "extra_body" not in captured


@pytest.mark.asyncio
async def test_loop_no_injection_without_level(monkeypatch):
    captured: dict = {}
    deps, bqc, QE, bec, ql = _make_loop_harness(monkeypatch, captured, "openai", None)
    config = bec(model="fake", tools=[], deps=deps)
    engine = QE(config, initial_messages=[{"role": "user", "content": "t"}])
    _ = [ev async for ev in ql(engine, bqc(session_id="s"))]
    assert "extra_body" not in captured
    assert set(captured) == {
        "messages", "tools", "model", "max_tokens", "temperature",
    }


@pytest.mark.asyncio
async def test_loop_end_to_end_with_model_config(monkeypatch):
    """不 mock 求值器：等级→get_model_config→映射→extra_body 全链路。"""
    captured: dict = {}
    deps, bqc, QE, bec, ql = _make_loop_harness(
        monkeypatch, captured, "openai", None  # 占位，下面覆盖真实 resolve
    )
    import query.loop as loop_module
    from query.utils.reasoning import resolve_reasoning_patch as real_resolve

    fake = ModelConfig(
        name="fake", context_window=1, max_output_tokens=1,
        reasoning_levels=("low", "medium"),
        reasoning_params_map='{"reasoning_effort": "{reasoningLevel}"}',
    )
    import startup.model.config as model_config_module
    monkeypatch.setattr(model_config_module, "get_model_config", lambda m: fake)
    monkeypatch.setattr(loop_module, "resolve_reasoning_patch", real_resolve)

    config = bec(model="fake", tools=[], reasoning_level="medium", deps=deps)
    engine = QE(config, initial_messages=[{"role": "user", "content": "t"}])
    _ = [ev async for ev in ql(engine, bqc(session_id="s"))]
    assert captured["extra_body"] == {"reasoning_effort": "medium"}


# ---------------------------------------------------------------------------
# Anthropic kwargs 合并（2.4 断言）
# ---------------------------------------------------------------------------


def test_anthropic_blacklist_allows_reasoning_keys():
    from query.services.api.anthropic_llm import (
        _OPENAI_ONLY_KEYS,
        _merge_kwargs,
    )

    assert "thinking" not in _OPENAI_ONLY_KEYS
    assert "reasoning_effort" not in _OPENAI_ONLY_KEYS
    payload: dict = {}
    _merge_kwargs(payload, {
        "thinking": {"type": "enabled"},
        "reasoning_effort": "low",
        "extra_body": {"should": "be-dropped"},
    })
    assert payload == {
        "thinking": {"type": "enabled"},
        "reasoning_effort": "low",
    }


# ---------------------------------------------------------------------------
# 路由层等级校验
# ---------------------------------------------------------------------------


def test_resolve_reasoning_level_route(monkeypatch):
    from server.routers.chat.routes import _resolve_reasoning_level
    import query.services.api.client as client_module
    import startup.model.config as model_config_module

    fake = ModelConfig(
        name="m", context_window=1, max_output_tokens=1,
        reasoning_levels=("low", "max"),
    )
    monkeypatch.setattr(client_module, "get_default_model", lambda: "m")
    monkeypatch.setattr(model_config_module, "get_model_config", lambda x: fake)

    assert _resolve_reasoning_level("low") == "low"
    assert _resolve_reasoning_level("nope") == ""
    assert _resolve_reasoning_level("") == ""
    assert _resolve_reasoning_level(None) == ""
    assert _resolve_reasoning_level(123) == ""
