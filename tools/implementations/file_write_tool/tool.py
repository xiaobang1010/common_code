"""Write 工具描述符装配。"""

from __future__ import annotations

from tools.implementations.file_write_tool.handler import (
    format_model_content,
    handle_write,
)
from tools.implementations.file_write_tool.schema import FileWriteInput
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

FILE_WRITE_PROMPT = """\
将文件写入本地文件系统。

使用说明：
- 此工具会覆盖指定路径上的现有文件
- 优先使用 Edit 工具修改现有文件——它只发送差异。仅使用此工具创建新文件或完全重写
- file_path 支持绝对路径或相对工作区的路径
- 覆盖已存在文件前须先用 Read 读取该文件（系统自动登记基线并校验，无需传 mtime/size）；从未读取过的文件会被拒绝
"""


async def _execute(inp: FileWriteInput, context: ToolUseContext) -> ToolResult:
    """执行入口 — handler 返回结构化结果，这里统一转为 ToolResult。"""
    try:
        structured = await handle_write(inp, context)
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
        return ToolResult(content=f"写入文件失败：{exc}", is_error=True)


def get_file_write_tool() -> Tool:
    """返回 Write 工具实例（携带完整描述符）。"""
    return build_tool(
        name="Write",
        description="写入文件",
        input_schema=FileWriteInput,
        execute=_execute,
        prompt=FILE_WRITE_PROMPT,
        # --- 声明式描述符 ---
        metadata=ToolMetadata(
            risk_level=RISK_MEDIUM,
            read_only=False,
            destructive=True,
            concurrent_safe=False,
            side_effect_scope=SCOPE_WORKSPACE,
            needs_approval=False,
        ),
        permission_spec=ToolPermissionSpec(
            permission="edit",
            reason="Write 通过文件系统创建或覆盖文件",
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
            user_visible_message="Write 在写入文件前被取消",
        ),
        format_model_content=format_model_content,
    )
