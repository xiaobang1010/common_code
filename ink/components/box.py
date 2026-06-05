"""Box 容器组件。

参考原始 TypeScript 实现: src/ink/components/Box.tsx

Box 是核心布局组件，类似浏览器中的 <div style="display: flex">。
支持 Flexbox 布局属性。
"""

from __future__ import annotations

from typing import Optional

from ..layout.node import LayoutNode


class Box:
    """容器组件，支持 Flexbox 布局。

    Attributes:
        flex_direction: 主轴方向 ("row" | "column")
        justify_content: 主轴对齐方式
        align_items: 交叉轴对齐方式
        flex_grow: 弹性增长因子
        flex_shrink: 弹性收缩因子
        flex_basis: 弹性基础尺寸
        width: 固定宽度
        height: 固定高度
        padding: 内边距（四边相同值）
        padding_top / padding_right / padding_bottom / padding_left: 单边内边距
        margin: 外边距（四边相同值）
        margin_top / margin_right / margin_bottom / margin_left: 单边外边距
        border_width: 边框宽度（四边相同值）
        border_top / border_right / border_bottom / border_left: 单边边框宽度
        gap: 子元素间距
        children: 子组件列表
    """

    def __init__(
        self,
        flex_direction: str = "column",
        justify_content: str = "flex-start",
        align_items: str = "stretch",
        flex_grow: float = 0,
        flex_shrink: float = 1,
        flex_basis: int | str = "auto",
        width: int | str = "auto",
        height: int | str = "auto",
        padding: int = 0,
        padding_top: Optional[int] = None,
        padding_right: Optional[int] = None,
        padding_bottom: Optional[int] = None,
        padding_left: Optional[int] = None,
        margin: int = 0,
        margin_top: Optional[int] = None,
        margin_right: Optional[int] = None,
        margin_bottom: Optional[int] = None,
        margin_left: Optional[int] = None,
        border_width: int = 0,
        border_top: Optional[int] = None,
        border_right: Optional[int] = None,
        border_bottom: Optional[int] = None,
        border_left: Optional[int] = None,
        gap: int = 0,
        children: Optional[list] = None,
    ) -> None:
        self.flex_direction = flex_direction
        self.justify_content = justify_content
        self.align_items = align_items
        self.flex_grow = flex_grow
        self.flex_shrink = flex_shrink
        self.flex_basis = flex_basis
        self.width = width
        self.height = height
        self.padding = padding
        self.padding_top = padding_top
        self.padding_right = padding_right
        self.padding_bottom = padding_bottom
        self.padding_left = padding_left
        self.margin = margin
        self.margin_top = margin_top
        self.margin_right = margin_right
        self.margin_bottom = margin_bottom
        self.margin_left = margin_left
        self.border_width = border_width
        self.border_top = border_top
        self.border_right = border_right
        self.border_bottom = border_bottom
        self.border_left = border_left
        self.gap = gap
        self.children = children or []

    def to_layout_node(self) -> LayoutNode:
        """转换为布局节点。"""
        props: dict = {
            "flexDirection": self.flex_direction,
            "justifyContent": self.justify_content,
            "alignItems": self.align_items,
            "flexGrow": self.flex_grow,
            "flexShrink": self.flex_shrink,
            "flexBasis": self.flex_basis,
            "width": self.width,
            "height": self.height,
            "padding": self.padding,
            "margin": self.margin,
            "borderWidth": self.border_width,
            "gap": self.gap,
        }

        # 单边 padding
        if self.padding_top is not None:
            props["paddingTop"] = self.padding_top
        if self.padding_right is not None:
            props["paddingRight"] = self.padding_right
        if self.padding_bottom is not None:
            props["paddingBottom"] = self.padding_bottom
        if self.padding_left is not None:
            props["paddingLeft"] = self.padding_left

        # 单边 margin
        if self.margin_top is not None:
            props["marginTop"] = self.margin_top
        if self.margin_right is not None:
            props["marginRight"] = self.margin_right
        if self.margin_bottom is not None:
            props["marginBottom"] = self.margin_bottom
        if self.margin_left is not None:
            props["marginLeft"] = self.margin_left

        # 单边 border
        if self.border_top is not None:
            props["borderTop"] = self.border_top
        if self.border_right is not None:
            props["borderRight"] = self.border_right
        if self.border_bottom is not None:
            props["borderBottom"] = self.border_bottom
        if self.border_left is not None:
            props["borderLeft"] = self.border_left

        children_nodes = []
        for child in self.children:
            if hasattr(child, "to_layout_node"):
                children_nodes.append(child.to_layout_node())
            elif isinstance(child, LayoutNode):
                children_nodes.append(child)

        return LayoutNode(
            type="box",
            props=props,
            children=children_nodes,
        )
