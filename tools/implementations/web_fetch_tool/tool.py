"""WebFetch 工具描述符装配。"""

from __future__ import annotations

from prompts.loader import load_tool_prompt
from tools.implementations.runtime.errors import ToolExecutionError
from tools.implementations.web_fetch_tool.handler import (
    format_model_content,
    handle_web_fetch,
)
from tools.implementations.web_fetch_tool.schema import WebFetchInput
from tools.protocol import (
    DIRECTION_HEAD,
    RISK_LOW,
    SCOPE_NETWORK,
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



async def _execute(inp: WebFetchInput, context: ToolUseContext) -> ToolResult:
    """执行入口——handler 返回结构化结果，这里统一转为 ToolResult。"""
    try:
        structured = await handle_web_fetch(inp)
        return ToolResult(
            content=format_model_content(structured),
            is_error=False,
            metadata=structured,
        )
    except ToolExecutionError as exc:
        return ToolResult(
            content=exc.message,
            is_error=True,
            metadata={"error_code": exc.code},
        )
    except Exception as exc:  # noqa: BLE001 工具执行兜底转可读错误
        return ToolResult(content=f"抓取网页失败：{exc}", is_error=True)


def get_web_fetch_tool() -> Tool:
    """返回 WebFetch 工具实例（携带完整描述符）。"""
    return build_tool(
        name="WebFetch",
        description="抓取 URL 内容并转 markdown",
        input_schema=WebFetchInput,
        execute=_execute,
        prompt=load_tool_prompt("web-fetch"),
        # 只读网络访问：不改本地状态，结果预算截断防灌爆上下文
        is_read_only=True,
        is_concurrent=True,
        requires_permission=False,
        metadata=ToolMetadata(
            risk_level=RISK_LOW,
            read_only=True,
            destructive=False,
            concurrent_safe=True,
            side_effect_scope=SCOPE_NETWORK,
            needs_approval=False,
        ),
        permission_spec=ToolPermissionSpec(
            permission="fetch",
            reason="WebFetch 仅发起网络读取，不改动本地状态",
            pattern_sources=["url"],
        ),
        result_budget=ResultBudget(
            max_model_chars=30000,
            preview_direction=DIRECTION_HEAD,
        ),
        timeout_policy=TimeoutPolicy(
            default_ms=40000,
            max_ms=40000,
            allow_call_override=False,
        ),
        cancellation=CancellationPolicy(
            supported=True,
            cleanup="best_effort",
            user_visible_message="WebFetch 在返回内容前被取消",
        ),
        format_model_content=format_model_content,
    )
