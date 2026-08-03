"""Glob 工具描述符装配。"""

from __future__ import annotations

from tools.implementations.glob_tool.handler import (
    format_model_content,
    handle_glob,
)
from tools.implementations.glob_tool.schema import GlobInput
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

GLOB_PROMPT = """\
快速文件模式匹配工具，适用于任何代码库大小。

使用说明：
- 支持 glob 模式，如 "**/*.js" 或 "src/**/*.ts"
- 返回按修改时间排序的匹配文件路径
- 当需要按名称模式查找文件时使用此工具
- path 支持绝对路径或相对工作区的路径，默认搜索整个工作区
"""


async def _execute(inp: GlobInput, context: ToolUseContext) -> ToolResult:
    """执行入口 — handler 返回结构化结果，这里统一转为 ToolResult。"""
    try:
        structured = await handle_glob(inp, context)
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
        return ToolResult(content=f"Glob 匹配失败：{exc}", is_error=True)


def get_glob_tool() -> Tool:
    """返回 Glob 工具实例（携带完整描述符）。"""
    return build_tool(
        name="Glob",
        description="文件模式匹配",
        input_schema=GlobInput,
        execute=_execute,
        prompt=GLOB_PROMPT,
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
            reason="Glob 只列出文件路径，无外部副作用",
            pattern_sources=["path", "pattern"],
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
            user_visible_message="Glob 在返回匹配结果前被取消",
        ),
        format_model_content=format_model_content,
    )
