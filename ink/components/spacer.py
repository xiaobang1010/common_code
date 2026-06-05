"""Spacer 弹性空间组件。

参考原始 TypeScript 实现: src/ink/components/Spacer.tsx

Spacer 是一个弹性空间组件，沿主轴方向填充所有可用空间。
等价于 <Box flexGrow={1} />。
"""

from __future__ import annotations

from ..layout.node import LayoutNode


class Spacer:
    """弹性空间组件。

    Attributes:
        flex_grow: 弹性增长因子 (默认 1)
    """

    def __init__(self, flex_grow: float = 1) -> None:
        self.flex_grow = flex_grow

    def to_layout_node(self) -> LayoutNode:
        """转换为布局节点。"""
        return LayoutNode(
            type="spacer",
            props={
                "flexGrow": self.flex_grow,
                "flexShrink": 0,
            },
        )
