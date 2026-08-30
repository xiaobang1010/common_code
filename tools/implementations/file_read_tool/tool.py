"""Read 工具描述符装配。"""

from __future__ import annotations

from tools.implementations.file_read_tool.handler import (
    format_model_content,
    handle_read,
)
from tools.implementations.file_read_tool.schema import FileReadInput
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

FILE_READ_PROMPT = """\
读取本地文件系统中的文件内容。

使用说明：
- file_path 支持绝对路径或相对工作区的路径
- 默认只读取前 2000 行；文件更大时需用 offset/limit 分段读取
- offset 为起始行号（从 1 开始），limit 为读取行数
- 结果使用 cat -n 格式，行号从 1 开始
- 此工具只能读取文件，不能读取目录
- 读取成功后系统自动登记文件基线，后续 Write/Edit 覆盖该文件时自动校验，无需手动回传 mtime/size
"""


async def _execute(inp: FileReadInput, context: ToolUseContext) -> ToolResult:
    """执行入口 — handler 返回结构化结果，这里统一转为 ToolResult。"""
    try:
        structured = await handle_read(inp, context)
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
        return ToolResult(content=f"读取文件失败：{exc}", is_error=True)


def get_file_read_tool() -> Tool:
    """返回 Read 工具实例（携带完整描述符）。"""
    return build_tool(
        name="Read",
        description="读取文件内容",
        input_schema=FileReadInput,
        execute=_execute,
        prompt=FILE_READ_PROMPT,
        is_read_only=True,
        is_concurrent=True,
        requires_permission=False,
        # --- 声明式描述符 ---
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
            reason="Read 只检查文件内容，无外部副作用",
            pattern_sources=["file_path"],
        ),
        result_budget=ResultBudget(
            max_model_chars=30000,
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
            user_visible_message="Read 在返回文件内容前被取消",
        ),
        format_model_content=format_model_content,
    )
