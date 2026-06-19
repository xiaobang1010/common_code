"""Flexbox 布局计算引擎。

参考原始 TypeScript 实现: src/ink/layout/engine.ts, src/ink/layout/yoga.ts

实现简化的 Flexbox 布局算法，支持 Ink 使用的子集：
- row 和 column 方向
- flex_grow / flex_shrink / flex_basis
- padding / margin / border
- gap
- justify_content / align_items
"""

from __future__ import annotations

from .node import ComputedLayout, EdgeValues, LayoutNode, StyleProps


class LayoutEngine:
    """计算组件树的布局。

    使用简化的 Flexbox 算法，分两趟完成：
    1. 自底向上测量：计算节点的固有尺寸
    2. 自顶向下分配：根据约束分配可用空间
    """

    def compute_layout(
        self, root: LayoutNode, max_width: int, max_height: int
    ) -> None:
        """递归计算所有节点的布局，结果写入 node.computed_layout。"""
        # 第一趟：自底向上测量固有尺寸
        self._measure_intrinsic(root, max_width, max_height)
        # 第二趟：自顶向下分配可用空间
        self._allocate(root, 0, 0, max_width, max_height)

    # ------------------------------------------------------------------
    # 固有尺寸测量
    # ------------------------------------------------------------------

    def _measure_intrinsic(
        self, node: LayoutNode, avail_w: int, avail_h: int
    ) -> tuple[int, int]:
        """自底向上测量节点的固有尺寸 (intrinsic width, intrinsic height)。

        返回 (intrinsic_w, intrinsic_h)，不含 margin。
        """
        style = node.style
        inner_w = self._content_width(avail_w, style)
        inner_h = self._content_height(avail_h, style)

        if node.type == "text":
            return self._measure_text(node, inner_w, inner_h)
        elif node.type == "spacer":
            return (0, 0)
        else:
            # box
            return self._measure_box(node, inner_w, inner_h, style)

    def _measure_text(
        self, node: LayoutNode, avail_w: int, avail_h: int
    ) -> tuple[int, int]:
        """测量文本节点的固有尺寸。"""
        content = node.props.get("children", "")
        if not isinstance(content, str):
            content = str(content)

        wrap = node.props.get("wrap", True)

        lines = content.split("\n")
        if wrap and avail_w > 0:
            wrapped_lines: list[str] = []
            for line in lines:
                if len(line) <= avail_w:
                    wrapped_lines.append(line)
                else:
                    wrapped_lines.extend(self._wrap_line(line, avail_w))
            lines = wrapped_lines

        width = max(len(line) for line in lines) if lines else 0
        height = len(lines)
        return (width, height)

    def _measure_box(
        self,
        node: LayoutNode,
        avail_w: int,
        avail_h: int,
        style: StyleProps,
    ) -> tuple[int, int]:
        """测量 box 节点的固有尺寸。"""
        if not node.children:
            return (0, 0)

        is_row = style.flex_direction == "row"
        gap = style.gap
        total_gap = gap * (len(node.children) - 1) if len(node.children) > 1 else 0

        child_sizes: list[tuple[int, int]] = []
        for child in node.children:
            cw, ch = self._measure_intrinsic(child, avail_w, avail_h)
            child_sizes.append((cw, ch))

        if is_row:
            total_w = sum(w for w, _ in child_sizes) + total_gap
            total_h = max(h for _, h in child_sizes) if child_sizes else 0
        else:
            total_w = max(w for w, _ in child_sizes) if child_sizes else 0
            total_h = sum(h for _, h in child_sizes) + total_gap

        return (total_w, total_h)

    # ------------------------------------------------------------------
    # 空间分配
    # ------------------------------------------------------------------

    def _allocate(
        self,
        node: LayoutNode,
        x: int,
        y: int,
        width: int,
        height: int,
    ) -> None:
        """自顶向下分配可用空间。"""
        style = node.style

        # margin 偏移
        x += style.margin.left
        y += style.margin.top
        width -= style.margin.horizontal
        height -= style.margin.vertical

        width = max(width, 0)
        height = max(height, 0)

        # 确定最终尺寸
        final_w = self._resolve_size(style.width, width)
        final_h = self._resolve_size(style.height, height)

        node.computed_layout = ComputedLayout(
            x=x, y=y, width=final_w, height=final_h
        )

        if node.type in ("text", "spacer") or not node.children:
            return

        # box: 分配子节点
        self._allocate_children(node, x, y, final_w, final_h, style)

    def _allocate_children(
        self,
        node: LayoutNode,
        box_x: int,
        box_y: int,
        box_w: int,
        box_h: int,
        style: StyleProps,
    ) -> None:
        """分配 box 子节点的位置和尺寸。"""
        is_row = style.flex_direction == "row"
        gap = style.gap

        # 内容区域 = box 尺寸 - padding - border
        content_x = box_x + style.padding.left + style.border.left
        content_y = box_y + style.padding.top + style.border.top
        content_w = box_w - style.padding.horizontal - style.border.horizontal
        content_h = box_h - style.padding.vertical - style.border.vertical
        content_w = max(content_w, 0)
        content_h = max(content_h, 0)

        children = node.children
        if not children:
            return

        n = len(children)
        total_gap = gap * (n - 1) if n > 1 else 0

        # 主轴 / 交叉轴
        if is_row:
            main_size = content_w
            cross_size = content_h
        else:
            main_size = content_h
            cross_size = content_w

        # 减去 gap
        available_main = main_size - total_gap
        available_main = max(available_main, 0)

        # 计算每个子节点的主轴基础尺寸
        child_bases = self._compute_child_bases(
            children, available_main, cross_size, is_row
        )

        # flex grow / shrink 分配剩余空间
        child_mains = self._distribute_flex(
            children, child_bases, available_main
        )

        # justify_content 分配起始偏移和间距
        positions = self._compute_justify_positions(
            child_mains, available_main, style.justify_content, gap, n
        )

        # 分配每个子节点
        for i, child in enumerate(children):
            child_main = child_mains[i]
            child_cross = self._compute_cross_size(
                child, cross_size, style.align_items, is_row
            )

            main_pos = positions[i]
            cross_pos = self._compute_cross_offset(
                child_cross, cross_size, style.align_items
            )

            if is_row:
                cx = content_x + main_pos
                cy = content_y + cross_pos
                cw = child_main
                ch = child_cross
            else:
                cx = content_x + cross_pos
                cy = content_y + main_pos
                cw = child_cross
                ch = child_main

            # 子节点的 margin 在 _allocate 中处理
            self._allocate(child, cx, cy, cw, ch)

    def _compute_child_bases(
        self,
        children: list[LayoutNode],
        available_main: int,
        cross_size: int,
        is_row: bool,
    ) -> list[int]:
        """计算每个子节点的主轴基础尺寸 (flex_basis 或固有尺寸)。"""
        bases: list[int] = []
        for child in children:
            style = child.style
            basis = style.flex_basis
            if isinstance(basis, int) and basis >= 0:
                bases.append(basis)
            else:
                # 使用固有尺寸
                if child.computed_layout is not None:
                    if is_row:
                        intrinsic = child.computed_layout.width
                    else:
                        intrinsic = child.computed_layout.height
                else:
                    # 重新测量
                    if is_row:
                        iw, _ = self._measure_intrinsic(child, available_main, cross_size)
                        intrinsic = iw
                    else:
                        _, ih = self._measure_intrinsic(child, cross_size, available_main)
                        intrinsic = ih
                # 减去 margin
                if is_row:
                    intrinsic -= style.margin.horizontal
                else:
                    intrinsic -= style.margin.vertical
                intrinsic = max(intrinsic, 0)
                bases.append(intrinsic)
        return bases

    def _distribute_flex(
        self,
        children: list[LayoutNode],
        bases: list[int],
        available_main: int,
    ) -> list[int]:
        """根据 flex_grow / flex_shrink 分配剩余空间。"""
        n = len(children)
        total_base = sum(bases)
        remaining = available_main - total_base

        result = list(bases)

        if remaining > 0:
            # flex_grow 分配
            total_grow = sum(children[i].style.flex_grow for i in range(n))
            if total_grow > 0:
                for i in range(n):
                    grow = children[i].style.flex_grow
                    if grow > 0:
                        result[i] = bases[i] + int(remaining * grow / total_grow)
        elif remaining < 0:
            # flex_shrink 收缩
            total_shrink = sum(
                children[i].style.flex_shrink * bases[i] for i in range(n)
            )
            if total_shrink > 0:
                for i in range(n):
                    shrink = children[i].style.flex_shrink
                    if shrink > 0 and bases[i] > 0:
                        shrink_amount = int(
                            abs(remaining) * shrink * bases[i] / total_shrink
                        )
                        result[i] = max(bases[i] - shrink_amount, 0)

        return result

    def _compute_justify_positions(
        self,
        child_mains: list[int],
        available_main: int,
        justify: str,
        gap: int,
        n: int,
    ) -> list[int]:
        """根据 justify_content 计算每个子节点的主轴位置。"""
        total_child_main = sum(child_mains)
        total_gap = gap * (n - 1) if n > 1 else 0
        free = available_main - total_child_main - total_gap

        positions: list[int] = []
        offset = 0

        if justify == "flex-start":
            offset = 0
        elif justify == "center":
            offset = free // 2
        elif justify == "flex-end":
            offset = free
        elif justify == "space-between":
            offset = 0
            if n > 1:
                between_gap = free / (n - 1) if n > 1 else 0
                pos = 0
                for i in range(n):
                    positions.append(int(pos))
                    pos += child_mains[i] + gap + between_gap
                return positions
        elif justify == "space-around":
            if n > 0:
                around = free / n
                pos = around / 2
                for i in range(n):
                    positions.append(int(pos))
                    pos += child_mains[i] + gap + around
                return positions
        elif justify == "space-evenly":
            if n > 0:
                evenly = free / (n + 1)
                pos = evenly
                for i in range(n):
                    positions.append(int(pos))
                    pos += child_mains[i] + gap + evenly
                return positions

        # flex-start / center / flex-end: 简单顺序排列
        pos = offset
        for i in range(n):
            positions.append(int(pos))
            pos += child_mains[i] + gap

        return positions

    def _compute_cross_size(
        self,
        child: LayoutNode,
        cross_available: int,
        align_items: str,
        is_row: bool,
    ) -> int:
        """计算子节点的交叉轴尺寸。"""
        style = child.style

        # 减去 margin
        cross_margin = style.margin.horizontal if is_row else style.margin.vertical
        effective_cross = max(cross_available - cross_margin, 0)

        if align_items == "stretch":
            # 如果子节点没有指定交叉轴尺寸，则拉伸
            if is_row:
                if isinstance(style.height, int):
                    return min(style.height, effective_cross)
                return effective_cross
            else:
                if isinstance(style.width, int):
                    return min(style.width, effective_cross)
                return effective_cross
        else:
            # 使用固有尺寸
            if is_row:
                if isinstance(style.height, int):
                    return min(style.height, effective_cross)
                if child.computed_layout:
                    return min(child.computed_layout.height, effective_cross)
                _, ih = self._measure_intrinsic(child, cross_available, cross_available)
                return min(ih, effective_cross)
            else:
                if isinstance(style.width, int):
                    return min(style.width, effective_cross)
                if child.computed_layout:
                    return min(child.computed_layout.width, effective_cross)
                iw, _ = self._measure_intrinsic(child, cross_available, cross_available)
                return min(iw, effective_cross)

    def _compute_cross_offset(
        self,
        child_cross: int,
        cross_available: int,
        align_items: str,
    ) -> int:
        """计算子节点在交叉轴上的偏移。"""
        if align_items == "stretch" or align_items == "flex-start":
            return 0
        elif align_items == "center":
            return (cross_available - child_cross) // 2
        elif align_items == "flex-end":
            return cross_available - child_cross
        return 0

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------

    @staticmethod
    def _content_width(avail_w: int, style: StyleProps) -> int:
        """计算内容可用宽度（减去 padding + border + margin）。"""
        return max(
            avail_w
            - style.padding.horizontal
            - style.border.horizontal
            - style.margin.horizontal,
            0,
        )

    @staticmethod
    def _content_height(avail_h: int, style: StyleProps) -> int:
        """计算内容可用高度（减去 padding + border + margin）。"""
        return max(
            avail_h
            - style.padding.vertical
            - style.border.vertical
            - style.margin.vertical,
            0,
        )

    @staticmethod
    def _resolve_size(
        value: int | str, available: int
    ) -> int:
        """解析尺寸值：auto 使用 available，具体数值直接使用。"""
        if isinstance(value, int):
            return min(value, available) if available > 0 else value
        return available

    @staticmethod
    def _wrap_line(line: str, max_width: int) -> list[str]:
        """将一行文本按 max_width 换行。"""
        if max_width <= 0:
            return [line]
        result: list[str] = []
        for i in range(0, len(line), max_width):
            result.append(line[i : i + max_width])
        return result


def compute_layout(
    root: LayoutNode, max_width: int, max_height: int
) -> None:
    """便捷函数：计算组件树的布局。"""
    engine = LayoutEngine()
    engine.compute_layout(root, max_width, max_height)
