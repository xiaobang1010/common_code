"""组件树到 Screen 缓冲区的绘制。

参考原始 TypeScript 实现: src/ink/renderer.ts, src/ink/render-node-to-output.ts

将组件树递归渲染到 Screen 缓冲区，支持 Box, Text, Spacer 组件。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Union

from .screen import Cell, CellWidth, Screen


# ---------------------------------------------------------------------------
# RenderNode 组件节点
# ---------------------------------------------------------------------------

@dataclass
class RenderNode:
    """组件节点。

    Attributes:
        type: 组件类型 ("box", "text", "spacer")
        props: 组件属性
        children: 子节点列表
        layout_info: 布局信息 (x, y, width, height)
    """
    type: str = "box"
    props: dict = field(default_factory=dict)
    children: list[RenderNode] = field(default_factory=list)
    layout_info: Optional[LayoutInfo] = None


@dataclass
class LayoutInfo:
    """布局信息。"""
    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------

class Renderer:
    """将组件树渲染到 Screen 缓冲区。

    支持的组件类型：
    - Box: 容器组件，支持 flexDirection, padding, border 等属性
    - Text: 文本组件，支持 fg, bg, bold, italic, underline 等样式
    - Spacer: 占位组件，填充空白区域
    """

    def render(self, node: RenderNode, screen: Screen) -> None:
        """递归渲染组件节点到 Screen 缓冲区。

        Args:
            node: 根组件节点
            screen: 目标 Screen 缓冲区
        """
        if node.layout_info is None:
            # 没有布局信息时使用整个屏幕
            node.layout_info = LayoutInfo(
                x=0, y=0,
                width=screen.width,
                height=screen.height,
            )

        self._render_node(node, screen)

    def _render_node(self, node: RenderNode, screen: Screen) -> None:
        """递归渲染单个节点。"""
        layout = node.layout_info
        if layout is None:
            return

        if node.type == "text":
            self._render_text(node, screen)
        elif node.type == "box":
            self._render_box(node, screen)
        elif node.type == "spacer":
            self._render_spacer(node, screen)

    def _render_text(self, node: RenderNode, screen: Screen) -> None:
        """渲染文本节点。"""
        layout = node.layout_info
        if layout is None:
            return

        text = node.props.get("children", "")
        if isinstance(text, list):
            # 子节点列表
            for child in node.children:
                self._render_node(child, screen)
            return

        if not isinstance(text, str):
            text = str(text)

        # 提取样式
        styles = self._extract_styles(node.props)

        # 写入文本到 Screen
        lines = text.split("\n")
        for line_idx, line in enumerate(lines):
            y = layout.y + line_idx
            if y >= screen.height:
                break

            x = layout.x
            for char in line:
                if x >= screen.width:
                    break
                screen.set_cell(x, y, char, **styles)
                x += 1

    def _render_box(self, node: RenderNode, screen: Screen) -> None:
        """渲染 Box 容器节点。"""
        layout = node.layout_info
        if layout is None:
            return

        # 绘制边框（如果有）
        border = node.props.get("borderStyle", "")
        border_fg = node.props.get("borderColor", "")

        if border and border != "none":
            self._render_border(node, screen, border, border_fg)

        # 渲染子节点
        for child in node.children:
            if child.layout_info is None:
                # 自动布局子节点
                child.layout_info = LayoutInfo(
                    x=layout.x,
                    y=layout.y,
                    width=layout.width,
                    height=layout.height,
                )
            self._render_node(child, screen)

    def _render_spacer(self, node: RenderNode, screen: Screen) -> None:
        """渲染 Spacer 占位节点（空白区域，无需写入）。"""
        pass

    def _render_border(self, node: RenderNode, screen: Screen,
                       style: str, fg: str) -> None:
        """渲染边框。"""
        layout = node.layout_info
        if layout is None:
            return

        # 简单的边框字符
        if style == "single":
            top_left, top_right = "┌", "┐"
            bottom_left, bottom_right = "└", "┘"
            horizontal, vertical = "─", "│"
        elif style == "double":
            top_left, top_right = "╔", "╗"
            bottom_left, bottom_right = "╚", "╝"
            horizontal, vertical = "═", "║"
        elif style == "round":
            top_left, top_right = "╭", "╮"
            bottom_left, bottom_right = "╰", "╯"
            horizontal, vertical = "─", "│"
        else:
            # bold
            top_left, top_right = "┏", "┓"
            bottom_left, bottom_right = "┗", "┛"
            horizontal, vertical = "━", "┃"

        border_styles: dict = {}
        if fg:
            border_styles["fg"] = fg

        x, y = layout.x, layout.y
        w, h = layout.width, layout.height

        # 顶边
        if y < screen.height and x < screen.width:
            screen.set_cell(x, y, top_left, **border_styles)
        for i in range(1, w - 1):
            if y < screen.height and x + i < screen.width:
                screen.set_cell(x + i, y, horizontal, **border_styles)
        if y < screen.height and x + w - 1 < screen.width:
            screen.set_cell(x + w - 1, y, top_right, **border_styles)

        # 左右边
        for j in range(1, h - 1):
            if y + j < screen.height and x < screen.width:
                screen.set_cell(x, y + j, vertical, **border_styles)
            if y + j < screen.height and x + w - 1 < screen.width:
                screen.set_cell(x + w - 1, y + j, vertical, **border_styles)

        # 底边
        if y + h - 1 < screen.height and x < screen.width:
            screen.set_cell(x, y + h - 1, bottom_left, **border_styles)
        for i in range(1, w - 1):
            if y + h - 1 < screen.height and x + i < screen.width:
                screen.set_cell(x + i, y + h - 1, horizontal, **border_styles)
        if y + h - 1 < screen.height and x + w - 1 < screen.width:
            screen.set_cell(x + w - 1, y + h - 1, bottom_right, **border_styles)

    def _extract_styles(self, props: dict) -> dict:
        """从属性中提取样式。"""
        styles: dict = {}
        if "fg" in props and props["fg"]:
            styles["fg"] = props["fg"]
        if "bg" in props and props["bg"]:
            styles["bg"] = props["bg"]
        if props.get("bold"):
            styles["bold"] = True
        if props.get("italic"):
            styles["italic"] = True
        if props.get("underline"):
            styles["underline"] = True
        if props.get("strikethrough"):
            styles["strikethrough"] = True
        if props.get("inverse"):
            styles["inverse"] = True
        return styles


# ---------------------------------------------------------------------------
# 便捷构建函数
# ---------------------------------------------------------------------------

def text(content: str, **styles) -> RenderNode:
    """创建文本节点。"""
    return RenderNode(
        type="text",
        props={"children": content, **styles},
    )


def box(children: Optional[list[RenderNode]] = None, **props) -> RenderNode:
    """创建 Box 容器节点。"""
    return RenderNode(
        type="box",
        props=props,
        children=children or [],
    )


def spacer() -> RenderNode:
    """创建 Spacer 占位节点。"""
    return RenderNode(type="spacer")
