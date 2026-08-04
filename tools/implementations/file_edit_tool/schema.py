"""Edit 工具输入模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class FileEditInput(BaseModel):
    """文件编辑工具输入。

    Attributes:
        file_path: 目标文件路径（绝对路径或相对工作区的路径）
        old_string: 要被替换的原始文本（空串表示创建新文件）
        new_string: 替换后的文本
        replace_all: 是否替换所有匹配项（默认 False，要求唯一匹配）
    """

    file_path: str = Field(description="要编辑的文件路径")
    old_string: str = Field(description="要被替换的原始文本")
    new_string: str = Field(description="替换后的文本")
    replace_all: bool = Field(default=False, description="是否替换所有匹配项")
