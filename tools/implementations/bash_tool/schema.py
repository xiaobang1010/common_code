"""Bash 工具输入模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class BashInput(BaseModel):
    """Bash 工具输入。

    Attributes:
        command: 要执行的 shell 命令
        timeout: 可选超时（毫秒），不得超过工具声明的上限，超过会被钳制
        description: 命令用途的简短描述（供 UI 展示）
    """

    command: str = Field(description="要执行的 shell 命令")
    timeout: int | None = Field(default=None, description="可选超时（毫秒）")
    description: str | None = Field(default=None, description="命令用途描述")
