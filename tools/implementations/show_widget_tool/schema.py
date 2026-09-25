"""show_widget 工具输入模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ShowWidgetInput(BaseModel):
    """show_widget 工具输入。

    Attributes:
        title: 可视化产物的简短标识
        widget_code: 原始 SVG/HTML 片段
        loading_messages: 渲染期间的加载提示（JSON 编码字符串数组）
    """

    title: str = Field(
        description=(
            "Short identifier for this visual, written in the same language the user "
            "is using. Must be specific and disambiguating — if the conversation has "
            "multiple visuals, this title alone should tell you which one is being "
            "referenced. Spaces and hyphens are converted to underscores "
            "automatically. Also used as the download filename."
        ),
    )
    widget_code: str = Field(
        description=(
            "SVG or HTML code to render. For SVG: raw code starting with <svg> tag. "
            "For HTML: raw HTML fragment, do NOT include DOCTYPE, <html>, <head>, or "
            "<body> tags."
        ),
    )
    loading_messages: str = Field(
        description=(
            "A JSON-encoded string array of 1–4 loading messages shown while the "
            "visual renders, each roughly 5 words long. Write in the same language "
            "the user is using. Example: '[\"Preparing chart data\",\"Rendering "
            "visualization\",\"Applying styles\",\"Almost ready\"]'"
        ),
    )
