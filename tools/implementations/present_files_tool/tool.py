"""present_files 工具描述符装配。"""

from __future__ import annotations

from prompts.loader import load_tool_prompt
from tools.implementations.present_files_tool.handler import (
    format_model_content,
    handle_present_files,
)
from tools.implementations.present_files_tool.schema import PresentFilesInput
from tools.implementations.runtime.errors import ToolExecutionError
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



async def _execute(inp: PresentFilesInput, context: ToolUseContext) -> ToolResult:
    """执行入口——handler 返回结构化结果，这里统一转为 ToolResult。"""
    try:
        structured = await handle_present_files(inp)
        return ToolResult(
            content=format_model_content(structured),
            is_error=False,
            metadata={"type": "present_files", **structured},
        )
    except ToolExecutionError as exc:
        return ToolResult(
            content=exc.message,
            is_error=True,
            metadata={"error_code": exc.code},
        )
    except Exception as exc:  # noqa: BLE001 工具执行兜底转可读错误
        return ToolResult(content=f"交付文件失败：{exc}", is_error=True)


def get_present_files_tool() -> Tool:
    """返回 present_files 工具实例（携带完整描述符）。"""
    return build_tool(
        name="present_files",
        description="向用户交付最终产物（面板打开文件）",
        input_schema=PresentFilesInput,
        execute=_execute,
        prompt=load_tool_prompt("present-files"),
        # 只读校验 + 前端呈现，无副作用
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
            reason="present_files 只校验文件并在面板中打开，不修改任何内容",
            pattern_sources=["files"],
        ),
        result_budget=ResultBudget(
            max_model_chars=2000,
            preview_direction=DIRECTION_HEAD,
        ),
        timeout_policy=TimeoutPolicy(
            default_ms=15000,
            max_ms=15000,
            allow_call_override=False,
        ),
        cancellation=CancellationPolicy(
            supported=True,
            cleanup="none",
            user_visible_message="present_files 在交付前被取消",
        ),
        format_model_content=format_model_content,
    )
