"""Grep 工具描述符装配。"""

from __future__ import annotations

from tools.implementations.grep_tool.handler import (
    format_model_content,
    handle_grep,
)
from tools.implementations.grep_tool.schema import GrepInput
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

GREP_PROMPT = """\
强大的内容搜索工具。

使用说明：
- 支持完整的正则表达式语法（如 "log.*Error", "function\\s+\\w+"）
- 使用 include 参数过滤文件（如 "*.js", "*.py"）
- 输出模式："content" 显示匹配行，"files_with_matches" 仅显示文件路径，"count" 显示匹配计数
- 默认输出模式为 "content"
- path 支持绝对路径或相对工作区的路径，默认搜索整个工作区
- 自动跳过 .git/node_modules/__pycache__/.venv 等目录
"""


async def _execute(inp: GrepInput, context: ToolUseContext) -> ToolResult:
    """执行入口 — handler 返回结构化结果，这里统一转为 ToolResult。"""
    try:
        structured = await handle_grep(inp, context)
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
        return ToolResult(content=f"Grep 搜索失败：{exc}", is_error=True)


def get_grep_tool() -> Tool:
    """返回 Grep 工具实例（携带完整描述符）。"""
    return build_tool(
        name="Grep",
        description="内容搜索",
        input_schema=GrepInput,
        execute=_execute,
        prompt=GREP_PROMPT,
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
            reason="Grep 只搜索文件内容，无外部副作用",
            pattern_sources=["path", "pattern"],
        ),
        result_budget=ResultBudget(
            max_model_chars=30000,
            preview_direction=DIRECTION_HEAD,
        ),
        timeout_policy=TimeoutPolicy(
            default_ms=60000,
            max_ms=60000,
            allow_call_override=False,
        ),
        cancellation=CancellationPolicy(
            supported=True,
            cleanup="none",
            user_visible_message="Grep 在返回搜索结果前被取消",
        ),
        format_model_content=format_model_content,
    )
