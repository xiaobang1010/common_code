"""present_files 工具输入模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class PresentFilesInput(BaseModel):
    """present_files 工具输入。

    Attributes:
        files: 要呈现的文件路径列表（按查看优先级排序）
        explanation: 一句话说明产出了什么
    """

    files: list[str] = Field(
        description="要呈现给用户的文件路径列表（绝对路径或相对工作区路径），"
        "按查看优先级排序：用户最先该看的放第一个，它会自动聚焦打开",
    )
    explanation: str = Field(
        default="",
        description="一句话说明产出了什么、为什么交付这些文件（展示在交付卡片上）",
    )
