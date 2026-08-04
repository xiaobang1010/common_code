"""Grep 工具输入模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class GrepInput(BaseModel):
    """Grep 工具输入。

    Attributes:
        pattern: 正则表达式搜索模式
        path: 搜索根目录（绝对路径或相对工作区的路径），默认工作区根
        include: 文件名过滤模式（如 "*.py"，逗号分隔多个）
        output_mode: content 匹配行 / files_with_matches 仅文件路径 / count 计数
    """

    pattern: str = Field(description="正则表达式搜索模式")
    path: str | None = Field(default=None, description="搜索根目录，默认工作区根")
    include: str | None = Field(default=None, description="文件名过滤模式，如 *.py")
    output_mode: str = Field(
        default="content",
        description="输出模式：content / files_with_matches / count",
    )
