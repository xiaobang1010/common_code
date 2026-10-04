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

    question: str = Field(
        description="向用户提出的问题文本：清晰具体、以问号结尾，一次只问真正卡住决策的一个问题"
    )
    options: list[QuestionOption] = Field(
        default_factory=list,
        description="候选选项（2-4 个）。仅当答案确实可枚举为少数几个合理选项时给；"
        "开放式问题留空，界面始终允许用户自由输入",
    )
