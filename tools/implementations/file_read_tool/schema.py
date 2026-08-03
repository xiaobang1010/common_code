"""Read 工具输入模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class FileReadInput(BaseModel):
    """文件读取工具输入。

    Attributes:
        file_path: 文件路径（绝对路径或相对工作区的路径）
        offset: 起始行号（从 1 开始），默认 1
        limit: 读取行数，默认全部
    """

    file_path: str = Field(description="要读取的文件路径")
    offset: int | None = Field(default=None, description="起始行号（从 1 开始）")
    limit: int | None = Field(default=None, description="读取行数")
