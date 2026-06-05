"""帧差分算法。

参考原始 TypeScript 实现: src/ink/log-update.ts

核心思想：比较前后帧的 Screen 缓冲区，仅输出变化部分。
使用 ANSI 光标定位序列实现部分更新，避免全屏重绘。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Optional

from .screen import Cell, CellWidth, Screen
from .terminal import (
    CSI,
    cursor_move,
    erase_lines,
)


# ---------------------------------------------------------------------------
# Diff Patch 类型
# ---------------------------------------------------------------------------

@dataclass
class PatchStdout:
    """写入 stdout 的文本。"""
    type: str = "stdout"
    content: str = ""


@dataclass
class PatchClear:
    """擦除行。"""
    type: str = "clear"
    count: int = 0


@dataclass
class PatchClearTerminal:
    """清除整个终端。"""
    type: str = "clearTerminal"
    reason: str = ""


@dataclass
class PatchCursorMove:
    """相对光标移动。"""
    type: str = "cursorMove"
    x: int = 0
    y: int = 0


@dataclass
class PatchCursorTo:
    """绝对列定位。"""
    type: str = "cursorTo"
    col: int = 0


@dataclass
class PatchCursorHide:
    """隐藏光标。"""
    type: str = "cursorHide"


@dataclass
class PatchCursorShow:
    """显示光标。"""
    type: str = "cursorShow"


@dataclass
class PatchCarriageReturn:
    """回车。"""
    type: str = "carriageReturn"


@dataclass
class PatchStyleStr:
    """ANSI 样式字符串。"""
    type: str = "styleStr"
    str: str = ""


# Diff 是 Patch 的列表
Diff = list


# ---------------------------------------------------------------------------
# Frame (简化版)
# ---------------------------------------------------------------------------

@dataclass
class FrameCursor:
    """帧光标状态。"""
    x: int = 0
    y: int = 0
    visible: bool = True


@dataclass
class FrameViewport:
    """帧视口。"""
    width: int = 80
    height: int = 24


@dataclass
class Frame:
    """渲染帧。"""
    screen: Screen
    viewport: FrameViewport
    cursor: FrameCursor


def empty_frame(rows: int = 24, cols: int = 80) -> Frame:
    """创建空帧。"""
    return Frame(
        screen=Screen(cols, rows),
        viewport=FrameViewport(cols, rows),
        cursor=FrameCursor(0, 0, True),
    )


# ---------------------------------------------------------------------------
# LogUpdate
# ---------------------------------------------------------------------------

class LogUpdate:
    """维护前帧缓存，计算前后帧差异。

    核心逻辑：比较新旧 Screen 缓冲区，仅输出变化部分。
    使用 ANSI 光标定位序列实现部分更新。
    """

    def __init__(self, stream=None, is_tty: bool = False) -> None:
        self._stream = stream or sys.stdout
        self._is_tty = is_tty
        self._previous_output: str = ""

    def execute(self, text: str) -> None:
        """输出文本，自动处理光标移动和差异计算。

        对于 TTY：使用差分更新。
        对于非 TTY：直接输出。
        """
        if not self._is_tty:
            self._stream.write(text + "\n")
            if hasattr(self._stream, "flush"):
                self._stream.flush()
            return

        diff = self._compute_diff(self._previous_output, text)
        self._stream.write(diff)
        if hasattr(self._stream, "flush"):
            self._stream.flush()
        self._previous_output = text

    def clear(self) -> None:
        """清除当前输出。"""
        if not self._previous_output:
            return

        line_count = self._previous_output.count("\n")
        if line_count > 0:
            self._stream.write(erase_lines(line_count))
            if hasattr(self._stream, "flush"):
                self._stream.flush()
        self._previous_output = ""

    def reset(self) -> None:
        """重置前帧缓存。"""
        self._previous_output = ""

    def render(self, prev: Frame, next_frame: Frame,
               alt_screen: bool = False) -> Diff:
        """比较前后帧，生成差分 Patch 列表。

        Args:
            prev: 前一帧
            next_frame: 当前帧
            alt_screen: 是否在替代屏幕模式

        Returns:
            Patch 列表
        """
        if not self._is_tty:
            return self._render_full_frame(next_frame)

        # 视口缩小或宽度变化时需要全量重绘
        if (next_frame.viewport.height < prev.viewport.height or
                (prev.viewport.width != 0 and
                 next_frame.viewport.width != prev.viewport.width)):
            return self._full_reset_sequence(next_frame, "resize")

        diff: Diff = []
        cursor_x = prev.cursor.x
        cursor_y = prev.cursor.y

        # 处理高度收缩：清除多余行
        height_delta = max(next_frame.screen.height, 1) - max(prev.screen.height, 1)
        if height_delta < 0:
            lines_to_clear = prev.screen.height - next_frame.screen.height
            if lines_to_clear > prev.viewport.height:
                return self._full_reset_sequence(next_frame, "offscreen")
            diff.append(PatchClear(count=lines_to_clear))
            diff.append(PatchCursorMove(x=0, y=-1))
            cursor_y -= lines_to_clear

        # 逐单元格比较
        prev_screen = prev.screen
        next_screen = next_frame.screen

        max_height = max(prev_screen.height, next_screen.height)
        max_width = max(prev_screen.width, next_screen.width)

        current_fg = ""
        current_bg = ""
        current_bold = False
        current_italic = False
        current_underline = False
        current_inverse = False

        for y in range(max_height):
            for x in range(max_width):
                prev_cell = prev_screen.get_cell(x, y) if y < prev_screen.height and x < prev_screen.width else None
                next_cell = next_screen.get_cell(x, y) if y < next_screen.height and x < next_screen.width else None

                # 跳过 SpacerTail
                if next_cell and next_cell.width == CellWidth.SPACER_TAIL:
                    continue
                if prev_cell and prev_cell.width == CellWidth.SPACER_TAIL and not next_cell:
                    continue

                # 跳过空白新增单元格
                if next_cell and next_cell.is_empty() and not prev_cell:
                    continue

                # 比较单元格
                if _cells_equal(prev_cell, next_cell):
                    continue

                # 需要更新此单元格
                _move_cursor(diff, cursor_x, cursor_y, x, y)
                cursor_x = x
                cursor_y = y

                if next_cell:
                    # 写入样式转换
                    style_str = _style_transition(
                        current_fg, current_bg, current_bold,
                        current_italic, current_underline, current_inverse,
                        next_cell.fg, next_cell.bg, next_cell.bold,
                        next_cell.italic, next_cell.underline, next_cell.inverse,
                    )
                    if style_str:
                        diff.append(PatchStyleStr(str=style_str))
                    current_fg = next_cell.fg
                    current_bg = next_cell.bg
                    current_bold = next_cell.bold
                    current_italic = next_cell.italic
                    current_underline = next_cell.underline
                    current_inverse = next_cell.inverse

                    diff.append(PatchStdout(content=next_cell.char))
                    char_width = 2 if next_cell.width == CellWidth.WIDE else 1
                    cursor_x += char_width
                elif prev_cell:
                    # 清除单元格
                    reset_str = _style_transition(
                        current_fg, current_bg, current_bold,
                        current_italic, current_underline, current_inverse,
                        "", "", False, False, False, False,
                    )
                    if reset_str:
                        diff.append(PatchStyleStr(str=reset_str))
                    current_fg = ""
                    current_bg = ""
                    current_bold = False
                    current_italic = False
                    current_underline = False
                    current_inverse = False
                    diff.append(PatchStdout(content=" "))
                    cursor_x += 1

        # 恢复光标位置
        if not alt_screen:
            if next_frame.cursor.y >= next_frame.screen.height:
                # 需要换行到达目标行
                diff.append(PatchCarriageReturn())
                rows_to_create = next_frame.cursor.y - cursor_y
                for _ in range(max(0, rows_to_create)):
                    diff.append(PatchStdout(content="\n"))
            else:
                _move_cursor(diff, cursor_x, cursor_y,
                             next_frame.cursor.x, next_frame.cursor.y)

        return diff

    def _render_full_frame(self, frame: Frame) -> Diff:
        """渲染完整帧（非 TTY 模式）。"""
        text = frame.screen.to_string()
        return [PatchStdout(content=text)]

    def _full_reset_sequence(self, frame: Frame, reason: str) -> Diff:
        """生成全量重置序列。"""
        diff: Diff = [PatchClearTerminal(reason=reason)]
        text = frame.screen.to_string()
        if text:
            diff.append(PatchStdout(content=text))
        return diff

    def _compute_diff(self, prev: str, next_text: str) -> str:
        """简单文本差分（用于 execute 方法）。"""
        if not prev:
            return next_text

        prev_lines = prev.split("\n")
        next_lines = next_text.split("\n")

        # 如果行数不同，需要清除旧输出
        if len(prev_lines) != len(next_lines):
            result = erase_lines(len(prev_lines))
            result += next_text
            return next_text

        # 简单策略：清除旧内容，写入新内容
        result = erase_lines(len(prev_lines))
        result += next_text
        return next_text


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _cells_equal(a: Optional[Cell], b: Optional[Cell]) -> bool:
    """比较两个单元格是否相等。"""
    if a is None and b is None:
        return True
    if a is None or b is None:
        # 两个都视为空白则相等
        a_empty = a is None or a.is_empty()
        b_empty = b is None or b.is_empty()
        return a_empty and b_empty
    return (a.char == b.char and
            a.fg == b.fg and
            a.bg == b.bg and
            a.bold == b.bold and
            a.italic == b.italic and
            a.underline == b.underline and
            a.strikethrough == b.strikethrough and
            a.inverse == b.inverse and
            a.hyperlink == b.hyperlink)


def _move_cursor(diff: Diff, from_x: int, from_y: int,
                 to_x: int, to_y: int) -> None:
    """生成光标移动 Patch。"""
    dx = to_x - from_x
    dy = to_y - from_y
    if dx == 0 and dy == 0:
        return

    if dy != 0:
        # 换行时先回车
        diff.append(PatchCarriageReturn())
        if dy != 0:
            diff.append(PatchCursorMove(x=to_x, y=dy))
    else:
        diff.append(PatchCursorMove(x=dx, y=0))


def _style_transition(prev_fg: str, prev_bg: str, prev_bold: bool,
                      prev_italic: bool, prev_underline: bool,
                      prev_inverse: bool,
                      next_fg: str, next_bg: str, next_bold: bool,
                      next_italic: bool, next_underline: bool,
                      next_inverse: bool) -> str:
    """计算样式转换的 ANSI 序列。"""
    from .screen import _fg_code, _bg_code

    parts: list[str] = []

    if prev_fg != next_fg:
        parts.append(_fg_code(next_fg))
    if prev_bg != next_bg:
        parts.append(_bg_code(next_bg))
    if prev_bold != next_bold:
        parts.append("\x1b[1m" if next_bold else "\x1b[22m")
    if prev_italic != next_italic:
        parts.append("\x1b[3m" if next_italic else "\x1b[23m")
    if prev_underline != next_underline:
        parts.append("\x1b[4m" if next_underline else "\x1b[24m")
    if prev_inverse != next_inverse:
        parts.append("\x1b[7m" if next_inverse else "\x1b[27m")

    return "".join(parts)


def write_diff_to_terminal(stream, diff: Diff) -> None:
    """将 Diff Patch 列表写入终端。"""
    if not diff:
        return

    buffer: list[str] = []
    for patch in diff:
        ptype = patch.type
        if ptype == "stdout":
            buffer.append(patch.content)
        elif ptype == "clear":
            if patch.count > 0:
                buffer.append(erase_lines(patch.count))
        elif ptype == "clearTerminal":
            buffer.append(CSI + "2J" + CSI + "H")
        elif ptype == "cursorMove":
            buffer.append(cursor_move(patch.x, patch.y))
        elif ptype == "cursorTo":
            col = patch.col
            buffer.append(f"{CSI}{col + 1}G")
        elif ptype == "cursorHide":
            buffer.append(CSI + "?25l")
        elif ptype == "cursorShow":
            buffer.append(CSI + "?25h")
        elif ptype == "carriageReturn":
            buffer.append("\r")
        elif ptype == "styleStr":
            buffer.append(patch.str)

    output = "".join(buffer)
    if output:
        stream.write(output)
        if hasattr(stream, "flush"):
            stream.flush()
