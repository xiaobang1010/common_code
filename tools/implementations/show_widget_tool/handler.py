"""show_widget 实现：入参校验与渲染载荷组装。

校验规则与失败文案逐字保留；载荷以 JSON 文本返回给模型，
同时经 metadata 交给执行层，由前端在对话流中内联渲染。
"""

from __future__ import annotations

import json
import re
from typing import Any

from tools.implementations.show_widget_tool.schema import ShowWidgetInput


def parse_loading_messages(raw: Any) -> list[str]:
    """解析 loading_messages：数组、JSON 编码字符串数组或逗号分隔字符串皆可。"""
    if isinstance(raw, list):
        return [item.strip() for item in (str(x) for x in raw) if item.strip()]
    if not isinstance(raw, str):
        return []
    trimmed = raw.strip()
    if not trimmed:
        return []
    if trimmed.startswith("["):
        try:
            parsed = json.loads(trimmed)
            if isinstance(parsed, list):
                return [item.strip() for item in (str(x) for x in parsed) if item.strip()]
        except Exception:  # noqa: BLE001 非 JSON 字符串按逗号分隔处理
            pass
    return [item for item in (s.strip() for s in trimmed.split(",")) if item]


def sanitize_title(title: str) -> str:
    """标题转安全标识：空白与连字符归一为下划线，仅保留字母数字下划线。"""
    s = re.sub(r"[\s-]+", "_", title)
    s = "".join(ch for ch in s if ch.isalnum() or ch == "_")
    s = re.sub(r"^_+|_+$", "", s)
    s = re.sub(r"_{2,}", "_", s)
    return s or "widget"


def validate_inputs(
    title: str | None, widget_code: str | None, loading_messages: list[str]
) -> str | None:
    """按既定规则校验入参，返回错误文案；通过则返回 None。"""
    if not title or not title.strip():
        return "title is required."
    if not widget_code or not widget_code.strip():
        return "widget_code is required."
    if len(loading_messages) == 0:
        return "loading_messages must contain at least one message."
    if len(loading_messages) > 4:
        return "loading_messages can contain at most four messages."
    code = widget_code.strip()
    if re.search(r"<(!DOCTYPE|html|head|body)\b", code, re.IGNORECASE):
        return "widget_code must be a raw SVG or HTML fragment without document wrapper tags."
    if re.search(r"\b(localStorage|sessionStorage)\b", code):
        return "widget_code cannot use localStorage or sessionStorage in the widget sandbox."
    if re.search(r"position\s*:\s*fixed", code, re.IGNORECASE):
        return "widget_code cannot use position: fixed because the widget height is auto-sized from in-flow content."
    if re.search(r"<form[\s>]", code, re.IGNORECASE):
        return "widget_code cannot use <form> tags. Use normal controls and event handlers instead."
    if code.startswith("<svg"):
        if len(re.findall(r"<svg[\s>]", code, re.IGNORECASE)) != 1:
            return "widget_code must contain exactly one <svg> element."
        if not re.search(r"""viewBox\s*=\s*["']0\s+0\s+680\s+\d+["']""", code, re.IGNORECASE):
            return "SVG widget_code must use a 680px-wide viewBox."
    return None


def build_error_payload(message: str) -> dict[str, Any]:
    """校验失败载荷。"""
    return {
        "type": "visualizer_show_widget_result",
        "success": False,
        "title": "",
        "loading_messages": [],
        "render_mode": "html",
        "message": message,
    }


def format_model_content(structured: dict[str, Any]) -> str:
    """结果 → 模型可读文本。

    widget_code 本就是模型在工具入参里写出的内容，回传时剔除以免重复占预算。
    """
    if structured.get("success"):
        slim = {k: v for k, v in structured.items() if k not in ("widget_code", "loading_messages")}
        return json.dumps(slim, ensure_ascii=False)
    return json.dumps(structured, ensure_ascii=False)


def handle_show_widget(inp: ShowWidgetInput) -> dict[str, Any]:
    """校验并组装渲染载荷。"""
    title = inp.title if isinstance(inp.title, str) else None
    widget_code = inp.widget_code if isinstance(inp.widget_code, str) else None
    loading_messages = parse_loading_messages(inp.loading_messages)
    error = validate_inputs(title, widget_code, loading_messages)
    if error:
        return build_error_payload(error)
    return {
        "type": "visualizer_show_widget_result",
        "success": True,
        "title": sanitize_title(title or ""),
        "widget_code": widget_code,
        "loading_messages": loading_messages,
        "render_mode": "svg" if (widget_code or "").strip().startswith("<svg") else "html",
        "message": "Widget payload prepared for inline rendering.",
    }
