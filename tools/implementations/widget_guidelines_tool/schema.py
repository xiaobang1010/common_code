"""widget_guidelines 工具输入模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class WidgetGuidelinesInput(BaseModel):
    """widget_guidelines 工具输入。

    Attributes:
        modules: 要加载的设计规范模块，数组或逗号分隔字符串皆可
    """

    modules: list[str] | str = Field(
        description=(
            'Which module(s) to load. Pick all that fit. Options: diagram, mockup, '
            'interactive, art, chart. Pass as an array (e.g. ["diagram","chart"]), '
            'a JSON-encoded string array, or a comma-separated list like "diagram,chart".'
        ),
    )
