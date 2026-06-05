"""布局节点定义。

参考原始 TypeScript 实现: src/ink/layout/node.ts

定义布局树的节点结构、计算后的布局结果和样式属性。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Union


@dataclass
class ComputedLayout:
    """计算后的布局结果。"""

    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0


@dataclass
class EdgeValues:
    """四边值（padding / margin / border）。"""

    top: int = 0
    right: int = 0
    bottom: int = 0
    left: int = 0

    @property
    def horizontal(self) -> int:
        return self.left + self.right

    @property
    def vertical(self) -> int:
        return self.top + self.bottom


@dataclass
class StyleProps:
    """样式属性，对应 Ink 使用的 Flexbox 子集。"""

    flex_direction: str = "column"  # "row" | "column"
    justify_content: str = "flex-start"
    align_items: str = "stretch"
    flex_grow: float = 0
    flex_shrink: float = 1
    flex_basis: Union[int, str] = "auto"  # int 或 "auto"
    width: Union[int, str] = "auto"
    height: Union[int, str] = "auto"
    padding: EdgeValues = field(default_factory=EdgeValues)
    margin: EdgeValues = field(default_factory=EdgeValues)
    border: EdgeValues = field(default_factory=EdgeValues)
    gap: int = 0


class LayoutNode:
    """布局树的节点。

    Attributes:
        type: 节点类型 ("box", "text", "spacer")
        props: 节点属性（样式、内容等）
        children: 子节点列表
        computed_layout: 计算后的布局
        style: 解析后的样式属性
    """

    def __init__(
        self,
        type: str = "box",
        props: Optional[dict] = None,
        children: Optional[list[LayoutNode]] = None,
    ) -> None:
        self.type = type
        self.props = props or {}
        self.children: list[LayoutNode] = children or []
        self.computed_layout: Optional[ComputedLayout] = None
        self.style = self._parse_style()

    def _parse_style(self) -> StyleProps:
        """从 props 中解析样式属性。"""
        s = StyleProps()

        s.flex_direction = self.props.get("flexDirection", "column")
        s.justify_content = self.props.get("justifyContent", "flex-start")
        s.align_items = self.props.get("alignItems", "stretch")
        s.flex_grow = float(self.props.get("flexGrow", 0))
        s.flex_shrink = float(self.props.get("flexShrink", 1))
        s.flex_basis = self.props.get("flexBasis", "auto")
        s.width = self.props.get("width", "auto")
        s.height = self.props.get("height", "auto")
        s.gap = int(self.props.get("gap", 0))

        # padding
        pad = self.props.get("padding", 0)
        s.padding = EdgeValues(
            top=int(self.props.get("paddingTop", pad)),
            right=int(self.props.get("paddingRight", pad)),
            bottom=int(self.props.get("paddingBottom", pad)),
            left=int(self.props.get("paddingLeft", pad)),
        )

        # margin
        mar = self.props.get("margin", 0)
        s.margin = EdgeValues(
            top=int(self.props.get("marginTop", mar)),
            right=int(self.props.get("marginRight", mar)),
            bottom=int(self.props.get("marginBottom", mar)),
            left=int(self.props.get("marginLeft", mar)),
        )

        # border
        brd = self.props.get("borderWidth", 0)
        s.border = EdgeValues(
            top=int(self.props.get("borderTop", brd)),
            right=int(self.props.get("borderRight", brd)),
            bottom=int(self.props.get("borderBottom", brd)),
            left=int(self.props.get("borderLeft", brd)),
        )

        return s

    def add_child(self, child: LayoutNode) -> None:
        self.children.append(child)

    def __repr__(self) -> str:
        cl = self.computed_layout
        cl_str = f" x={cl.x} y={cl.y} w={cl.width} h={cl.height}" if cl else ""
        return f"LayoutNode({self.type!r}{cl_str}, children={len(self.children)})"
