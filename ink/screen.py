"""Screen 缓冲区管理。

参考原始 TypeScript 实现: src/ink/screen.ts

使用简化的 Python 实现：每个 Cell 是一个 dataclass 对象，
存储在二维列表中。与 TS 版本的 Int32Array 打包方式不同，
Python 版本优先可读性和正确性。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Cell 宽度枚举
# ---------------------------------------------------------------------------

class CellWidth:
    """单元格宽度分类。"""
    NARROW = 0       # 单宽字符
    WIDE = 1         # 双宽字符 (CJK, emoji)
    SPACER_TAIL = 2  # 双宽字符的第二列占位
    SPACER_HEAD = 3  # 软换行时双宽字符的行尾占位


# ---------------------------------------------------------------------------
# Cell
# ---------------------------------------------------------------------------

@dataclass
class Cell:
    """屏幕上的单个字符单元。"""
    char: str = " "
    fg: str = ""           # 前景色, e.g. "red", "#ff0000", "rgb(255,0,0)"
    bg: str = ""           # 背景色
    bold: bool = False
    italic: bool = False
    underline: bool = False
    strikethrough: bool = False
    inverse: bool = False
    width: int = CellWidth.NARROW
    hyperlink: Optional[str] = None

    def is_empty(self) -> bool:
        """判断是否为空白单元格。"""
        return (
            self.char == " "
            and not self.fg
            and not self.bg
            and not self.bold
            and not self.italic
            and not self.underline
            and not self.strikethrough
            and not self.inverse
            and self.hyperlink is None
        )

    def clone(self) -> Cell:
        """创建副本。"""
        return Cell(
            char=self.char,
            fg=self.fg,
            bg=self.bg,
            bold=self.bold,
            italic=self.italic,
            underline=self.underline,
            strikethrough=self.strikethrough,
            inverse=self.inverse,
            width=self.width,
            hyperlink=self.hyperlink,
        )


# ---------------------------------------------------------------------------
# Damage 追踪 (脏区域)
# ---------------------------------------------------------------------------

@dataclass
class DamageRect:
    """脏区域矩形。"""
    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0


# ---------------------------------------------------------------------------
# Screen
# ---------------------------------------------------------------------------

class Screen:
    """管理终端屏幕的字符缓冲区。

    每个位置存储一个 Cell 对象，包含字符和样式信息。
    支持双缓冲：前后帧各持有一个 Screen 实例。
    """

    def __init__(self, width: int = 80, height: int = 24) -> None:
        self._width = width
        self._height = height
        self._buffer: list[list[Cell]] = []
        self.damage: Optional[DamageRect] = None
        self._init_buffer()

    def _init_buffer(self) -> None:
        """初始化缓冲区为空白单元格。"""
        self._buffer = []
        for _ in range(self._height):
            row: list[Cell] = [Cell() for _ in range(self._width)]
            self._buffer.append(row)
        self.damage = None

    @property
    def width(self) -> int:
        return self._width

    @property
    def height(self) -> int:
        return self._height

    def clear(self) -> None:
        """清空缓冲区。"""
        for y in range(self._height):
            for x in range(self._width):
                self._buffer[y][x] = Cell()
        self.damage = DamageRect(0, 0, self._width, self._height)

    def resize(self, width: int, height: int) -> None:
        """调整缓冲区大小，保留已有内容。"""
        if width == self._width and height == self._height:
            return

        new_buffer: list[list[Cell]] = []
        for y in range(height):
            row: list[Cell] = []
            for x in range(width):
                if y < self._height and x < self._width:
                    row.append(self._buffer[y][x].clone())
                else:
                    row.append(Cell())
            new_buffer.append(row)

        self._width = width
        self._height = height
        self._buffer = new_buffer
        self.damage = DamageRect(0, 0, width, height)

    def get_cell(self, x: int, y: int) -> Optional[Cell]:
        """获取指定位置的单元格。"""
        if x < 0 or y < 0 or x >= self._width or y >= self._height:
            return None
        return self._buffer[y][x]

    def set_cell(self, x: int, y: int, char: str, **styles) -> None:
        """设置指定位置的单元格。

        Args:
            x: 列坐标 (0-indexed)
            y: 行坐标 (0-indexed)
            char: 字符
            **styles: 样式属性 (fg, bg, bold, italic, underline, strikethrough,
                      inverse, width, hyperlink)
        """
        if x < 0 or y < 0 or x >= self._width or y >= self._height:
            return

        cell = Cell(char=char)
        for key, value in styles.items():
            if hasattr(cell, key):
                setattr(cell, key, value)

        # 处理双宽字符的 SpacerTail
        prev_cell = self._buffer[y][x]
        if prev_cell.width == CellWidth.WIDE and cell.width != CellWidth.WIDE:
            # 清除之前的 SpacerTail
            if x + 1 < self._width:
                spacer = self._buffer[y][x + 1]
                if spacer.width == CellWidth.SPACER_TAIL:
                    self._buffer[y][x + 1] = Cell()

        self._buffer[y][x] = cell

        # 双宽字符自动创建 SpacerTail
        if cell.width == CellWidth.WIDE and x + 1 < self._width:
            self._buffer[y][x + 1] = Cell(width=CellWidth.SPACER_TAIL)

        # 更新脏区域
        self._expand_damage(x, y)

    def set_cell_style(self, x: int, y: int, **styles) -> None:
        """仅更新指定位置的样式，不改变字符。"""
        if x < 0 or y < 0 or x >= self._width or y >= self._height:
            return
        cell = self._buffer[y][x]
        if cell.width == CellWidth.SPACER_TAIL:
            return
        for key, value in styles.items():
            if hasattr(cell, key) and key != "char" and key != "width":
                setattr(cell, key, value)
        self._expand_damage(x, y)

    def _expand_damage(self, x: int, y: int) -> None:
        """扩展脏区域以包含 (x, y)。"""
        if self.damage is None:
            self.damage = DamageRect(x, y, 1, 1)
        else:
            d = self.damage
            min_x = min(d.x, x)
            min_y = min(d.y, y)
            max_x = max(d.x + d.width, x + 1)
            max_y = max(d.y + d.height, y + 1)
            self.damage = DamageRect(min_x, min_y, max_x - min_x, max_y - min_y)

    def blit_from(self, src: Screen, src_x: int = 0, src_y: int = 0,
                  dst_x: int = 0, dst_y: int = 0,
                  width: Optional[int] = None, height: Optional[int] = None) -> None:
        """从另一个 Screen 复制区域到当前 Screen。"""
        w = width if width is not None else min(src.width - src_x, self._width - dst_x)
        h = height if height is not None else min(src.height - src_y, self._height - dst_y)

        for dy in range(h):
            sy = src_y + dy
            dy_dst = dst_y + dy
            if sy < 0 or sy >= src.height or dy_dst < 0 or dy_dst >= self._height:
                continue
            for dx in range(w):
                sx = src_x + dx
                dx_dst = dst_x + dx
                if sx < 0 or sx >= src.width or dx_dst < 0 or dx_dst >= self._width:
                    continue
                self._buffer[dy_dst][dx_dst] = src._buffer[sy][sx].clone()

        # 更新脏区域
        if w > 0 and h > 0:
            self._expand_damage(dst_x, dst_y)
            self._expand_damage(dst_x + w - 1, dst_y + h - 1)

    def to_string(self) -> str:
        """将缓冲区转换为带 ANSI 样式的字符串。"""
        lines: list[str] = []
        for y in range(self._height):
            line = self._render_row(y)
            lines.append(line.rstrip())
        return "\n".join(lines)

    def _render_row(self, y: int) -> str:
        """渲染单行为 ANSI 字符串。"""
        parts: list[str] = []
        current_fg = ""
        current_bg = ""
        current_bold = False
        current_italic = False
        current_underline = False
        current_inverse = False

        for x in range(self._width):
            cell = self._buffer[y][x]

            # 跳过 SpacerTail
            if cell.width == CellWidth.SPACER_TAIL:
                continue

            # 生成样式转换序列
            style_parts: list[str] = []

            if cell.fg != current_fg:
                style_parts.append(_fg_code(cell.fg))
                current_fg = cell.fg
            if cell.bg != current_bg:
                style_parts.append(_bg_code(cell.bg))
                current_bg = cell.bg
            if cell.bold != current_bold:
                style_parts.append("\x1b[1m" if cell.bold else "\x1b[22m")
                current_bold = cell.bold
            if cell.italic != current_italic:
                style_parts.append("\x1b[3m" if cell.italic else "\x1b[23m")
                current_italic = cell.italic
            if cell.underline != current_underline:
                style_parts.append("\x1b[4m" if cell.underline else "\x1b[24m")
                current_underline = cell.underline
            if cell.inverse != current_inverse:
                style_parts.append("\x1b[7m" if cell.inverse else "\x1b[27m")
                current_inverse = cell.inverse

            if style_parts:
                parts.append("".join(style_parts))

            parts.append(cell.char)

        # 行尾重置样式
        reset = _reset_code(current_fg, current_bg, current_bold,
                            current_italic, current_underline, current_inverse)
        if reset:
            parts.append(reset)

        return "".join(parts)


# ---------------------------------------------------------------------------
# ANSI 颜色辅助
# ---------------------------------------------------------------------------

# ANSI 16色名称映射
_ANSI_COLORS = {
    "black": "0", "red": "1", "green": "2", "yellow": "3",
    "blue": "4", "magenta": "5", "cyan": "6", "white": "7",
    "bright_black": "8", "bright_red": "9", "bright_green": "10",
    "bright_yellow": "11", "bright_blue": "12", "bright_magenta": "13",
    "bright_cyan": "14", "bright_white": "15",
}


def _fg_code(color: str) -> str:
    """生成前景色 ANSI 序列。"""
    if not color:
        return "\x1b[39m"  # 默认前景色
    if color in _ANSI_COLORS:
        n = int(_ANSI_COLORS[color])
        if n <= 7:
            return f"\x1b[{30 + n}m"
        return f"\x1b[38;5;{n}m"
    if color.startswith("#") and len(color) == 7:
        r = int(color[1:3], 16)
        g = int(color[3:5], 16)
        b = int(color[5:7], 16)
        return f"\x1b[38;2;{r};{g};{b}m"
    return "\x1b[39m"


def _bg_code(color: str) -> str:
    """生成背景色 ANSI 序列。"""
    if not color:
        return "\x1b[49m"  # 默认背景色
    if color in _ANSI_COLORS:
        n = int(_ANSI_COLORS[color])
        if n <= 7:
            return f"\x1b[{40 + n}m"
        return f"\x1b[48;5;{n}m"
    if color.startswith("#") and len(color) == 7:
        r = int(color[1:3], 16)
        g = int(color[3:5], 16)
        b = int(color[5:7], 16)
        return f"\x1b[48;2;{r};{g};{b}m"
    return "\x1b[49m"


def _reset_code(fg: str, bg: str, bold: bool, italic: bool,
                underline: bool, inverse: bool) -> str:
    """生成样式重置序列。"""
    parts: list[str] = []
    if fg:
        parts.append("\x1b[39m")
    if bg:
        parts.append("\x1b[49m")
    if bold:
        parts.append("\x1b[22m")
    if italic:
        parts.append("\x1b[23m")
    if underline:
        parts.append("\x1b[24m")
    if inverse:
        parts.append("\x1b[27m")
    return "".join(parts)
