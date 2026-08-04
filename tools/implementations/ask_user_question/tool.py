"""AskUserQuestion 工具描述符装配。"""

from __future__ import annotations

from tools.implementations.ask_user_question.handler import (
    format_model_content,
    handle_ask_user_question,
)
from tools.implementations.ask_user_question.schema import AskUserQuestionInput
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

ASK_USER_QUESTION_PROMPT = """\
在执行任务过程中向用户提出问题，挂起等待用户回答后继续。

使用说明：
- 当遇到多种合理方案需要用户拍板、需求有歧义、或需要收集用户偏好时使用
- question 应清晰具体，以问号结尾
- options 可选：提供 2-4 个候选选项，每项含 label（简短显示文本）和 description（说明）
- 工具会阻塞直到用户回答，回答文本将作为工具结果返回
- 不要用它询问可以通过读代码/搜索自行解决的问题
"""


async def _execute(inp: AskUserQuestionInput, context: ToolUseContext) -> ToolResult:
    """执行入口 — handler 返回结构化结果，这里统一转为 ToolResult。"""
    try:
        structured = await handle_ask_user_question(inp, context)
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
        return ToolResult(content=f"提问失败：{exc}", is_error=True)


def get_ask_user_question_tool() -> Tool:
    """返回 AskUserQuestion 工具实例（携带完整描述符）。"""
    return build_tool(
        name="AskUserQuestion",
        description="向用户提问并等待回答",
        input_schema=AskUserQuestionInput,
        execute=_execute,
        prompt=ASK_USER_QUESTION_PROMPT,
        is_read_only=True,
        is_concurrent=False,
        requires_permission=False,
        # --- 声明式描述符 ---
        # 只读低风险：提问本身就是征询，任意权限模式下都自动放行
        metadata=ToolMetadata(
            risk_level=RISK_LOW,
            read_only=True,
            destructive=False,
            concurrent_safe=False,
            side_effect_scope=SCOPE_NONE,
            needs_approval=False,
        ),
        permission_spec=ToolPermissionSpec(
            permission="read",
            reason="AskUserQuestion 只与用户交互，无外部副作用",
            pattern_sources=["question"],
        ),
        result_budget=ResultBudget(
            max_model_chars=2000,
            preview_direction=DIRECTION_HEAD,
        ),
        timeout_policy=TimeoutPolicy(
            # 等待用户回答可能较久，给足上限（10 分钟）
            default_ms=600000,
            max_ms=600000,
            allow_call_override=False,
        ),
        cancellation=CancellationPolicy(
            supported=True,
            cleanup="none",
            user_visible_message="提问在收到用户回答前被取消",
        ),
        format_model_content=format_model_content,
    )
