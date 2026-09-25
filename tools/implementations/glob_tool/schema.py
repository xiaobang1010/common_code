"""Glob 工具输入模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class GlobInput(BaseModel):
    """Glob 工具输入。

    Attributes:
        pattern: glob 匹配模式（如 "**/*.py"）
        path: 搜索根目录（绝对路径或相对工作区的路径），默认工作区根
    """

    pattern: str = Field(
        description="glob 匹配模式，如 **/*.py；按名字/后缀找文件用本工具，搜内容改用 Grep"
    )
    path: str | None = Field(
        default=None,
        description="搜索根目录。仅在限定子目录搜索时给，默认搜整个工作区",
    )
