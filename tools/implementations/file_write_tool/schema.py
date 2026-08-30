"""Write 工具输入模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class FileWriteInput(BaseModel):
    """文件写入工具输入。

    Attributes:
        file_path: 目标文件路径（绝对路径或相对工作区的路径）
        content: 要写入的完整文件内容
        base_mtime: 可选，一般无需传；覆盖前校验的基线 mtime（缺省自动采用系统登记值）
        base_size: 可选，一般无需传；覆盖前校验的基线 size（缺省自动采用系统登记值）
    """

    file_path: str = Field(description="要写入的文件路径")
    content: str = Field(description="要写入的文件内容")
    base_mtime: int | None = Field(
        default=None,
        description="可选，一般无需传；覆盖前校验的基线 mtime（缺省自动采用系统登记值）",
    )
    base_size: int | None = Field(
        default=None,
        description="可选，一般无需传；覆盖前校验的基线 size（缺省自动采用系统登记值）",
    )
