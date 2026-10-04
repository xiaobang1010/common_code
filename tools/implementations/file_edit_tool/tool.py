"""Edit 工具描述符装配。"""

from __future__ import annotations

from prompts.loader import load_tool_prompt
from tools.implementations.file_edit_tool.handler import (
    format_model_content,
    handle_edit,
)
from tools.implementations.file_edit_tool.schema import FileEditInput
from tools.implementations.runtime.errors import ToolExecutionError
from tools.protocol import (
    DIRECTION_HEAD,
    RISK_MEDIUM,
    SCOPE_WORKSPACE,
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



async def _execute(inp: FileEditInput, context: ToolUseContext) -> ToolResult:
    """执行入口 — handler 返回结构化结果，这里统一转为 ToolResult。"""
    try:
        structured = await handle_edit(inp, context)
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
    except Exception as exc:
        return ToolResult(content=f"编辑文件失败：{exc}", is_error=True)


def get_file_edit_tool() -> Tool:
    """返回 Edit 工具实例（携带完整描述符）。"""
    return build_tool(
        name="Edit",
        description="编辑文件（搜索替换）",
        input_schema=FileEditInput,
        execute=_execute,
        prompt=load_tool_prompt("edit"),
        # --- 声明式描述符 ---
        metadata=ToolMetadata(
            risk_level=RISK_MEDIUM,
            read_only=False,
            destructive=False,
            concurrent_safe=False,
            side_effect_scope=SCOPE_WORKSPACE,
            needs_approval=False,
        ),
        permission_spec=ToolPermissionSpec(
            permission="edit",
            reason="Edit 通过文件系统修改文件内容",
            pattern_sources=["file_path"],
        ),
        result_budget=ResultBudget(
            max_model_chars=2000,
            preview_direction=DIRECTION_HEAD,
        ),
        timeout_policy=TimeoutPolicy(
            default_ms=30000,
            max_ms=30000,
            allow_call_override=False,
        ),
        cancellation=CancellationPolicy(
            supported=True,
            cleanup="none",
            user_visible_message="Edit 在修改文件前被取消",
        ),
        format_model_content=format_model_content,
    )
