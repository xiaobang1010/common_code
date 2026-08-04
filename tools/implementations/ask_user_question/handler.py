"""AskUserQuestion 工具核心逻辑。

调用 context.question_callback 向用户提问并挂起等待回答，
回答文本作为结构化结果返回给模型。
"""

from __future__ import annotations

from tools.implementations.ask_user_question.schema import AskUserQuestionInput
from tools.implementations.runtime.errors import ToolExecutionError
from tools.protocol import ToolUseContext


async def handle_ask_user_question(
    inp: AskUserQuestionInput,
    context: ToolUseContext,
) -> dict:
    """向用户提问并等待回答。

    Args:
        inp: 工具输入（question + options）
        context: 工具执行上下文，携带 question_callback 提问通道

    Returns:
        结构化结果 {"question": ..., "answer": ...}

    Raises:
        ToolExecutionError: 当前环境没有提问通道（无前端/无回调）时抛出
    """
    callback = getattr(context, "question_callback", None)
    if callback is None:
        raise ToolExecutionError(
            "no_question_channel",
            "当前环境没有可用的提问通道（无前端界面），"
            "请直接在回复文本中向用户提出问题。",
        )

    options = [
        {"label": opt.label, "description": opt.description}
        for opt in inp.options
    ]
    answer = await callback(inp.question, options)
    return {
        "question": inp.question,
        "answer": answer,
    }


def format_model_content(structured: dict) -> str:
    """把结构化结果格式化为面向模型的文本。"""
    return f"用户回答：{structured.get('answer', '')}"
