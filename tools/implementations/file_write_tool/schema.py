"""Write 工具输入模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class FileWriteInput(BaseModel):
    """文件写入工具输入。

    Attributes:
        file_path: 目标文件路径（绝对路径或相对工作区的路径）
        content: 要写入的完整文件内容
        base_mtime: 覆盖已存在文件时需回传的基线 mtime（整数秒，来自最近一次 Read）
        base_size: 覆盖已存在文件时需回传的基线 size（字节，来自最近一次 Read）
    """

    file_path: str = Field(description="要写入的文件路径")
    content: str = Field(description="要写入的文件内容")
    base_mtime: int | None = Field(
        default=None,
        description="覆盖已存在文件时必须回传的基线 mtime（整数秒，来自最近一次 Read）",
    )
    base_size: int | None = Field(
        default=None,
        description="覆盖已存在文件时必须回传的基线 size（字节，来自最近一次 Read）",
    )
