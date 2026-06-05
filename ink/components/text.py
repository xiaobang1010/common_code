"""Text 文本组件。

参考原始 TypeScript 实现: src/ink/components/Text.tsx

Text 组件用于显示文本，支持颜色和样式属性。
文本根据容器宽度自动换行。
"""

from __future__ import annotations

from ..layout.node import LayoutNode


class Text:
    """文本组件。

    Attributes:
        content: 文本内容
        fg: 前景色
        bg: 背景色
        bold: 是否粗体
        italic: 是否斜体
        underline: 是否下划线
        strikethrough: 是否删除线
        inverse: 是否反色
        wrap: 是否自动换行 (默认 True)
    """

    def __init__(
        self,
        content: str = "",
        fg: str = "",
        bg: str = "",
        bold: bool = False,
        italic: bool = False,
        underline: bool = False,
        strikethrough: bool = False,
        inverse: bool = False,
        wrap: bool = True,
    ) -> None:
        self.content = content
        self.fg = fg
        self.bg = bg
        self.bold = bold
        self.italic = italic
        self.underline = underline
        self.strikethrough = strikethrough
        self.inverse = inverse
        self.wrap = wrap

    def to_layout_node(self) -> LayoutNode:
        """转换为布局节点。"""
        props: dict = {
            "children": self.content,
            "wrap": self.wrap,
            "flexGrow": 0,
            "flexShrink": 1,
        }

        if self.fg:
            props["fg"] = self.fg
        if self.bg:
            props["bg"] = self.bg
        if self.bold:
            props["bold"] = True
        if self.italic:
            props["italic"] = True
        if self.underline:
            props["underline"] = True
        if self.strikethrough:
            props["strikethrough"] = True
        if self.inverse:
            props["inverse"] = True

        return LayoutNode(
            type="text",
            props=props,
        )
