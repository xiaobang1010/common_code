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

    pattern: str = Field(
        description="正则表达式搜索模式（ripgrep 语法）；搜文件名用 Glob，本工具搜内容"
    )
    path: str | None = Field(
        default=None,
        description="搜索根目录。仅在限定子目录搜索时给，默认搜整个工作区",
    )
    include: str | None = Field(
        default=None,
        description="文件名过滤模式（如 *.py，逗号分隔多个）。仅当要限定文件类型时给",
    )
    output_mode: str = Field(
        default="content",
        description="content 显示匹配行带行号（要引用行级证据时用）；"
        "files_with_matches 只列文件（先看命中分布再决定读哪个）；count 计数（评估规模用）",
    )
