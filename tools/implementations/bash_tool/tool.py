"""Bash 工具描述符装配。"""

from __future__ import annotations

from tools.implementations.bash_tool.handler import (
    format_model_content,
    handle_bash,
)
from tools.implementations.bash_tool.schema import BashInput
from tools.implementations.runtime.errors import ToolExecutionError
from tools.protocol import (
    DIRECTION_TAIL,
    RISK_HIGH,
    SCOPE_SYSTEM,
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

BASH_PROMPT = """\
执行 shell 命令并返回输出。

使用说明：
- 工作目录在命令之间保持不变，但 shell 状态不会持久化
- 始终用双引号包裹包含空格的文件路径
- 尽量在会话中通过使用绝对路径来维持当前工作目录
- 可以指定可选的超时时间（毫秒），默认 120000ms（2 分钟），上限 600000ms（10 分钟）
- 发出多个命令时：
  - 如果命令相互独立且可以并行运行，在一条消息中发起多个 Bash 工具调用
  - 如果命令相互依赖且必须按顺序运行，使用 '&&' 将它们串联
"""

# 超时策略：默认 2 分钟，上限 10 分钟，允许调用覆盖（钳制到上限）
BASH_TIMEOUT_POLICY = TimeoutPolicy(
    default_ms=120000,
    max_ms=600000,
    allow_call_override=True,
)


async def _execute(inp: BashInput, context: ToolUseContext) -> ToolResult:
    """执行入口 — handler 返回结构化结果，这里统一转为 ToolResult。"""
    try:
        structured = await handle_bash(inp, context, BASH_TIMEOUT_POLICY)
        content = format_model_content(structured)
        # 超时或非零退出码视为错误结果
        is_error = structured.get("timed_out", False) or (
            structured.get("exit_code") not in (0, None)
        )
        return ToolResult(content=content, is_error=is_error, metadata=structured)
    except ToolExecutionError as exc:
        return ToolResult(
            content=exc.message,
            is_error=True,
            metadata={"error_code": exc.code},
        )
    except Exception as exc:
        return ToolResult(content=f"命令执行失败：{exc}", is_error=True)


def get_bash_tool() -> Tool:
    """返回 Bash 工具实例（携带完整描述符）。"""
    return build_tool(
        name="Bash",
        description="执行 shell 命令",
        input_schema=BashInput,
        execute=_execute,
        prompt=BASH_PROMPT,
        # --- 声明式描述符 ---
        metadata=ToolMetadata(
            risk_level=RISK_HIGH,
            read_only=False,
            destructive=False,
            concurrent_safe=False,
            side_effect_scope=SCOPE_SYSTEM,
            needs_approval=True,
        ),
        permission_spec=ToolPermissionSpec(
            permission="bash",
            reason="Bash 可运行子进程，可能影响工作区、git、网络或系统状态",
            pattern_sources=["command"],
        ),
        result_budget=ResultBudget(
            max_model_chars=30000,
            strategy="truncate",
            preview_direction=DIRECTION_TAIL,
        ),
        timeout_policy=BASH_TIMEOUT_POLICY,
        cancellation=CancellationPolicy(
            supported=True,
            cleanup="best_effort",
            user_visible_message="Bash 已取消，子进程已被要求停止",
        ),
        format_model_content=format_model_content,
    )
