"""AskUserQuestion 工具输入模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class QuestionOption(BaseModel):
    """单个候选选项。

    Attributes:
        label: 选项的简短显示文本（1-5 词）
        description: 选项的说明，解释选择该项的含义或影响
    """

    label: str = Field(description="选项的简短显示文本（1-5 词）")
    description: str = Field(default="", description="选项说明，解释该选择的含义或影响")


class AskUserQuestionInput(BaseModel):
    """AskUserQuestion 工具输入。

    Attributes:
        question: 向用户提出的问题文本，应以问号结尾
        options: 候选选项列表（可选，2-4 个），供用户快速选择
    """

    question: str = Field(description="向用户提出的问题文本")
    options: list[QuestionOption] = Field(
        default_factory=list,
        description="候选选项列表（可选，2-4 个）",
    )
