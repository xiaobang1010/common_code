"""TodoWrite 工具输入模型。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class TodoItem(BaseModel):
    """单条清单项。"""

    content: str = Field(description="要做的事，一句话写清楚（动宾结构）")
    status: Literal["pending", "in_progress", "completed"] = Field(
        description="pending 未开始 / in_progress 进行中 / completed 已完成"
    )


class TodoWriteInput(BaseModel):
    """TodoWrite 工具输入。

    Attributes:
        name: 清单名（kebab-case），同一任务的所有更新必须沿用同一名字
        todos: 完整清单（全量覆盖写入，不是增量追加）
    """

    name: str = Field(
        description="清单名，kebab-case（不含 .md 与路径分隔符）。"
        "同一任务的每次更新必须用同一个名字，否则会拆成多份清单",
    )
    todos: list[TodoItem] = Field(
        description="完整任务清单：每次调用都传全量条目（含未变的），系统整体覆盖写入；"
        "做完一项立即把该项置为 completed，不要攒批",
    )
