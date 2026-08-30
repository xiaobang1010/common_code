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
        base_mtime: 可选，覆盖前校验的基线 mtime（整数秒，来自最近一次 Read）
        base_size: 可选，覆盖前校验的基线 size（字节，来自最近一次 Read）
    """

    file_path: str = Field(description="要编辑的文件路径")
    old_string: str = Field(description="要被替换的原始文本")
    new_string: str = Field(description="替换后的文本")
    replace_all: bool = Field(default=False, description="是否替换所有匹配项")
    base_mtime: int | None = Field(
        default=None,
        description="可选，一般无需传；写回前校验的基线 mtime（缺省自动采用系统登记值）",
    )
    base_size: int | None = Field(
        default=None,
        description="可选，一般无需传；写回前校验的基线 size（缺省自动采用系统登记值）",
    )
