"""widget_guidelines 实现：按模块组装可视化设计规范并返回。

结果以 JSON 文本返回给模型（type=visualizer_read_me_result），
正文较长，工具描述符中通过结果预算放开截断。
"""

from __future__ import annotations

import json
from typing import Any

from tools.implementations.widget_guidelines_tool.guidelines import (
    build_guidelines,
    parse_modules,
)
from tools.implementations.widget_guidelines_tool.schema import WidgetGuidelinesInput


def handle_widget_guidelines(inp: WidgetGuidelinesInput) -> dict[str, Any]:
    """解析模块列表并组装规范全文。"""
    try:
        modules = parse_modules(inp.modules)
        return {
            "type": "visualizer_read_me_result",
            "content": build_guidelines(modules),
        }
    except Exception as e:  # noqa: BLE001 组装失败转为模型可读错误
        return {
            "type": "visualizer_read_me_result",
            "content": "",
            "success": False,
            "message": f"Failed to load visualizer guidance: {e}",
        }


def format_model_content(structured: dict[str, Any]) -> str:
    """结果 → 模型可读文本（JSON 序列化，保留中文等非 ASCII 字符）。"""
    return json.dumps(structured, ensure_ascii=False)
