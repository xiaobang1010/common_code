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

    file_path: str = Field(
        description="要读取的文件路径（绝对路径或相对工作区的路径；不支持目录）"
    )
    offset: int | None = Field(
        default=None,
        description="起始行号（从 1 开始）。仅当文件超出默认读取量、需要读其余部分时给",
    )
    limit: int | None = Field(
        default=None,
        description="读取行数。仅在分段读取时与 offset 配合给出，普通文件省略",
    )
