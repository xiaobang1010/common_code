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

    command: str = Field(
        description="要执行的 shell 命令（必须是 Windows 下 pwsh/powershell、类 Unix 下 sh 兼容的语法）"
    )
    timeout: int | None = Field(
        default=None,
        description="超时毫秒数。仅当命令预计超过 2 分钟时显式给足（上限 10 分钟，超过被钳制）；"
        "普通命令省略即可",
    )
    description: str | None = Field(
        default=None,
        description="一句话命令用途，供界面展示（建议填写：让用户看得懂每次执行在干什么）",
    )
