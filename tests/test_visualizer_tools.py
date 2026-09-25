"""可视化工具族测试：规范组装顺序、入参解析、校验文案、载荷结构与注册。"""

from __future__ import annotations

import asyncio
import json

from tools.implementations.show_widget_tool.handler import (
    parse_loading_messages,
    sanitize_title,
    validate_inputs,
)
from tools.implementations.show_widget_tool.schema import ShowWidgetInput
from tools.implementations.show_widget_tool.tool import get_show_widget_tool
from tools.implementations.widget_guidelines_tool.guidelines import (
    build_guidelines,
    normalize_modules,
    parse_modules,
)
from tools.implementations.widget_guidelines_tool.handler import (
    handle_widget_guidelines,
)
from tools.implementations.widget_guidelines_tool.schema import WidgetGuidelinesInput
from tools.implementations.widget_guidelines_tool.tool import (
    get_widget_guidelines_tool,
)
from tools.utils.schema import tool_to_openai_schema

SVG_OK = '<svg viewBox="0 0 680 520" width="100%"><rect class="box" x="40" y="40" width="200" height="44"/><text class="t" x="60" y="60">A</text></svg>'


# ---------------------------------------------------------------------------
# widget_guidelines
# ---------------------------------------------------------------------------


def test_parse_modules_accepts_three_shapes():
    assert parse_modules("diagram,chart") == ["diagram", "chart"]
    assert parse_modules('["diagram", "art"]') == ["diagram", "art"]
    assert parse_modules([" mockup ", "interactive", ""]) == ["mockup", "interactive"]


def test_normalize_dedup_and_rejects_unknown():
    assert normalize_modules(["diagram", "diagram", "bogus", None]) == ["diagram"]


def test_guidelines_section_composition_order():
    # 公共段恒在前；SVG 设置与 UI 组件按触发条件插入；模块段按给定顺序追加
    text = build_guidelines(["chart", "diagram"])
    sections = [line for line in text.splitlines() if line.startswith("# ")]
    assert sections == [
        "# Visualizer Core Design System",
        "# Color Palette (9 ramps × 7 levels)",
        "# SVG Setup Rules",
        "# UI Components",
        "# Charts (Chart.js)",
        "# Geographic maps (D3 choropleth)",
        "# Diagram Guidance",
    ]
    assert "# Interactive Guidance" not in text


def test_handler_returns_verbatim_structure():
    r = handle_widget_guidelines(WidgetGuidelinesInput(modules="art"))
    assert r["type"] == "visualizer_read_me_result"
    assert "# Art and illustration" in r["content"]
    assert "viewBox fixed" in r["content"]


def test_widget_guidelines_registered_and_described():
    tool = get_widget_guidelines_tool()
    schema = tool_to_openai_schema(tool)
    assert schema["function"]["description"] == tool.prompt
    assert "Do NOT mention or narrate this call" in tool.prompt
    assert "widget_guidelines" in [t.name for t in __import__("tools").get_tools()]


# ---------------------------------------------------------------------------
# show_widget
# ---------------------------------------------------------------------------


def test_parse_loading_messages_shapes():
    assert parse_loading_messages('["a","b"]') == ["a", "b"]
    assert parse_loading_messages(["a", " b "]) == ["a", "b"]
    assert parse_loading_messages("a,b,,c") == ["a", "b", "c"]
    assert parse_loading_messages(None) == []


def test_sanitize_title_rules():
    assert sanitize_title("系统架构 — 分层视图") == "系统架构_分层视图"
    assert sanitize_title("  hello--world  ") == "hello_world"
    assert sanitize_title("!!!") == "widget"


def test_validate_inputs_all_branches():
    assert validate_inputs("t", SVG_OK, ["渲染"]) is None
    assert validate_inputs("", SVG_OK, ["x"]) == "title is required."
    assert validate_inputs("t", "  ", ["x"]) == "widget_code is required."
    assert validate_inputs("t", SVG_OK, []) == "loading_messages must contain at least one message."
    assert (
        validate_inputs("t", SVG_OK, ["1", "2", "3", "4", "5"])
        == "loading_messages can contain at most four messages."
    )
    assert "wrapper tags" in validate_inputs("t", "<!DOCTYPE html><p>x</p>", ["x"])
    assert "sandbox" in validate_inputs("t", "<script>localStorage.setItem('a',1)</script>", ["x"])
    assert "position: fixed" in validate_inputs("t", "<div style='POSITION: FIXED'></div>", ["x"])
    assert "<form>" in validate_inputs("t", "<form action='/x'></form>", ["x"])
    assert "exactly one <svg>" in validate_inputs("t", SVG_OK + SVG_OK, ["x"])
    assert "680px-wide" in validate_inputs("t", '<svg viewBox="0 0 700 300"></svg>', ["x"])


def _run_show(title: str, code: str, msgs: str):
    tool = get_show_widget_tool()
    return asyncio.run(tool.execute(ShowWidgetInput(title=title, widget_code=code, loading_messages=msgs), None))


def test_show_widget_success_payload_and_metadata():
    r = _run_show("架构", SVG_OK, '["绘制架构","完成"]')
    assert not r.is_error
    payload = json.loads(r.content)
    assert payload["type"] == "visualizer_show_widget_result"
    assert payload["success"] is True
    # 模型侧回传剔除正文重复内容，但保留结构确认
    assert "widget_code" not in payload
    assert payload["render_mode"] == "svg"
    assert r.metadata["render_mode"] == "svg"


def test_show_widget_error_is_error_result():
    r = _run_show("x", "<html><body></body></html>", '["a"]')
    assert r.is_error
    payload = json.loads(r.content)
    assert payload["success"] is False and "wrapper tags" in payload["message"]


def test_show_widget_registered_with_verbatim_prompt():
    tool = get_show_widget_tool()
    schema = tool_to_openai_schema(tool)
    assert schema["function"]["description"] == tool.prompt
    assert "renders inline alongside your text response" in tool.prompt
    assert "show_widget" in [t.name for t in __import__("tools").get_tools()]
