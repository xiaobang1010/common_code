"""show_widget 工具描述符装配。"""

from __future__ import annotations

import json

from tools.implementations.runtime.errors import ToolExecutionError
from tools.implementations.show_widget_tool.handler import (
    format_model_content,
    handle_show_widget,
)
from tools.implementations.show_widget_tool.schema import ShowWidgetInput
from tools.protocol import (
    DIRECTION_HEAD,
    RISK_LOW,
    SCOPE_NONE,
    CancellationPolicy,
    ResultBudget,
    TimeoutPolicy,
    Tool,
    ToolMetadata,
    ToolPermissionSpec,
    ToolResult,
    ToolUseContext,
    build_tool,
)

# 发给模型的描述词（逐字）
_TOOL_PROMPT = (
    "Show visual content — SVG graphics, diagrams, charts, or interactive HTML "
    "widgets — that renders inline alongside your text response. Call "
    "widget_guidelines (modules: diagram|mockup|interactive|chart|art) before your "
    "first show_widget call to load required design guidance. widget_code MUST be a "
    'raw SVG/HTML fragment (no <html>/<head>/<body>/<!DOCTYPE>); for SVG use a '
    'viewBox starting with "0 0 680 ".'
)


async def _execute(inp: ShowWidgetInput, context: ToolUseContext) -> ToolResult:
    """执行入口——handler 返回结构化载荷，这里统一转为 ToolResult。"""
    try:
        payload = handle_show_widget(inp)
        failed = not payload.get("success")
        return ToolResult(
            content=format_model_content(payload),
            is_error=failed,
            metadata={
                "type": "show_widget",
                "success": payload.get("success"),
                "title": payload.get("title", ""),
                "render_mode": payload.get("render_mode", ""),
            },
        )
    except ToolExecutionError as exc:
        return ToolResult(content=exc.message, is_error=True, metadata={"error_code": exc.code})
    except Exception as exc:  # noqa: BLE001 工具执行兜底转可读错误
        return ToolResult(
            content=json.dumps(
                {
                    "type": "visualizer_show_widget_result",
                    "success": False,
                    "title": "",
                    "loading_messages": [],
                    "render_mode": "html",
                    "message": str(exc),
                },
                ensure_ascii=False,
            ),
            is_error=True,
        )


def get_show_widget_tool() -> Tool:
    """返回 show_widget 工具实例（携带完整描述符）。"""
    return build_tool(
        name="show_widget",
        description="在回答中内联展示可视化图形",
        input_schema=ShowWidgetInput,
        execute=_execute,
        prompt=_TOOL_PROMPT,
        # 纯载荷校验组装，渲染发生在前端，无副作用
        is_read_only=True,
        is_concurrent=True,
        requires_permission=False,
        metadata=ToolMetadata(
            risk_level=RISK_LOW,
            read_only=True,
            destructive=False,
            concurrent_safe=True,
            side_effect_scope=SCOPE_NONE,
            needs_approval=False,
        ),
        permission_spec=ToolPermissionSpec(
            permission="read",
            reason="show_widget 只校验并透传渲染载荷，不修改任何内容",
        ),
        # 成功回传为精简 JSON（正文已在模型入参中），失败回传为短文案
        result_budget=ResultBudget(
            max_model_chars=2000,
            preview_direction=DIRECTION_HEAD,
        ),
        timeout_policy=TimeoutPolicy(
            default_ms=5000,
            max_ms=5000,
            allow_call_override=False,
        ),
        cancellation=CancellationPolicy(
            supported=True,
            cleanup="none",
            user_visible_message="show_widget 在返回前被取消",
        ),
        format_model_content=format_model_content,
    )
