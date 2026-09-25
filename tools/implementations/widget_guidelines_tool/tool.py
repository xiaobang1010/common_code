"""widget_guidelines 工具描述符装配。"""

from __future__ import annotations

import json

from tools.implementations.runtime.errors import ToolExecutionError
from tools.implementations.widget_guidelines_tool.handler import (
    handle_widget_guidelines,
)
from tools.implementations.widget_guidelines_tool.schema import WidgetGuidelinesInput
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
    "Returns required context for show_widget (CSS variables, colors, typography, "
    "layout rules, examples). Call before your first show_widget call. Call again "
    "later if you need a different module. Do NOT mention or narrate this call to "
    "the user — it is an internal setup step."
)


async def _execute(inp: WidgetGuidelinesInput, context: ToolUseContext) -> ToolResult:
    """执行入口——纯文本组装，无副作用。"""
    try:
        payload = await _run(inp)
        return ToolResult(
            content=json.dumps(payload, ensure_ascii=False),
            is_error=False,
            metadata={"type": "widget_guidelines"},
        )
    except ToolExecutionError as exc:
        return ToolResult(content=exc.message, is_error=True, metadata={"error_code": exc.code})
    except Exception as exc:  # noqa: BLE001 工具执行兜底转可读错误
        return ToolResult(content=f"加载可视化设计规范失败：{exc}", is_error=True)


async def _run(inp: WidgetGuidelinesInput) -> dict[str, object]:
    return handle_widget_guidelines(inp)


def get_widget_guidelines_tool() -> Tool:
    """返回 widget_guidelines 工具实例（携带完整描述符）。"""
    return build_tool(
        name="widget_guidelines",
        description="加载可视化设计规范（供 show_widget 使用）",
        input_schema=WidgetGuidelinesInput,
        execute=_execute,
        prompt=_TOOL_PROMPT,
        # 纯读取组装，无副作用
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
            reason="widget_guidelines 只返回规范文本，不修改任何内容",
        ),
        # 规范全文可达数万字，放开模型侧截断
        result_budget=ResultBudget(
            max_model_chars=60000,
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
            user_visible_message="widget_guidelines 在返回前被取消",
        ),
    )
