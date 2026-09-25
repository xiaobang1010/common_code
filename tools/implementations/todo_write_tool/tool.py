"""TodoWrite 工具描述符装配。"""

from __future__ import annotations

from prompts.loader import load_tool_prompt
from tools.implementations.runtime.errors import ToolExecutionError
from tools.implementations.todo_write_tool.handler import (
    format_model_content,
    handle_todo_write,
)
from tools.implementations.todo_write_tool.schema import TodoWriteInput
from tools.protocol import (
    DIRECTION_HEAD,
    RISK_LOW,
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



async def _execute(inp: TodoWriteInput, context: ToolUseContext) -> ToolResult:
    """执行入口——handler 返回结构化结果，这里统一转为 ToolResult。"""
    try:
        structured = await handle_todo_write(inp)
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
        return ToolResult(content=f"更新清单失败：{exc}", is_error=True)


def get_todo_write_tool() -> Tool:
    """返回 TodoWrite 工具实例（携带完整描述符）。"""
    return build_tool(
        name="TodoWrite",
        description="维护任务清单（写 .agent/todos/ 并实时显示进展）",
        input_schema=TodoWriteInput,
        execute=_execute,
        prompt=load_tool_prompt("todo-write"),
        # 写的是 .agent/todos/ 记账文件，属工作区低风险操作，不打扰用户审批
        is_read_only=False,
        is_concurrent=False,
        requires_permission=False,
        metadata=ToolMetadata(
            risk_level=RISK_LOW,
            read_only=False,
            destructive=False,
            concurrent_safe=False,
            side_effect_scope=SCOPE_WORKSPACE,
            needs_approval=False,
        ),
        permission_spec=ToolPermissionSpec(
            permission="edit",
            reason="TodoWrite 写入工作区 .agent/todos/ 清单文件",
            pattern_sources=["name"],
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
            user_visible_message="TodoWrite 在写盘前被取消",
        ),
        format_model_content=format_model_content,
    )
