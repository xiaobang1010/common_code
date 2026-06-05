"""终端能力检测和底层操作。

参考原始 TypeScript 实现: src/ink/terminal.ts, src/ink/termio/
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# ANSI 转义序列常量
# ---------------------------------------------------------------------------

# CSI (Control Sequence Introducer)
ESC = "\x1b"
CSI = ESC + "["

# DEC 私有模式序列
ENTER_ALT_SCREEN = CSI + "?1049h"
EXIT_ALT_SCREEN = CSI + "?1049l"

ENABLE_MOUSE_TRACKING = CSI + "?1000h"  # 基本鼠标追踪
DISABLE_MOUSE_TRACKING = CSI + "?1000l"

ENABLE_MOUSE_TRACKING_ALL = CSI + "?1003h"  # 全鼠标追踪 (motion)
DISABLE_MOUSE_TRACKING_ALL = CSI + "?1003l"

ENABLE_FOCUS_REPORTING = CSI + "?1004h"
DISABLE_FOCUS_REPORTING = CSI + "?1004l"

ENABLE_BRACKETED_PASTE = CSI + "?2004h"
DISABLE_BRACKETED_PASTE = CSI + "?2004l"

# Kitty 键盘协议
ENABLE_KITTY_KEYBOARD = CSI + ">1u"
DISABLE_KITTY_KEYBOARD = CSI + ">0u"

# xterm modifyOtherKeys
ENABLE_MODIFY_OTHER_KEYS = CSI + ">4;2m"
DISABLE_MODIFY_OTHER_KEYS = CSI + ">4;0m"

# 光标
HIDE_CURSOR = CSI + "?25l"
SHOW_CURSOR = CSI + "?25h"

# 擦除
ERASE_SCREEN = CSI + "2J"
ERASE_LINE = CSI + "2K"

# 光标定位
CURSOR_HOME = CSI + "H"


# ---------------------------------------------------------------------------
# 同步输出 (DEC 2026)
# ---------------------------------------------------------------------------

BSU = CSI + "?2026h"  # Begin Synchronized Update
ESU = CSI + "?2026l"  # End Synchronized Update


# ---------------------------------------------------------------------------
# 终端能力检测
# ---------------------------------------------------------------------------

# 已知支持 DEC 2026 同步输出的终端
_SYNC_OUTPUT_TERMINALS = frozenset({
    "iTerm.app",
    "WezTerm",
    "WarpTerminal",
    "ghostty",
    "contour",
    "vscode",
    "alacritty",
})

# 已知支持扩展键报告的终端
_EXTENDED_KEYS_TERMINALS = frozenset({
    "iTerm.app",
    "kitty",
    "WezTerm",
    "ghostty",
    "tmux",
    "windows-terminal",
})


@dataclass
class TerminalCapabilities:
    """终端支持的能力集合。"""

    alt_screen: bool = False
    mouse_tracking: bool = False
    kitty_keyboard: bool = False
    modify_other_keys: bool = False
    true_color: bool = False
    unicode: bool = True
    synchronized_output: bool = False
    focus_reporting: bool = False
    bracketed_paste: bool = False

    # 环境信息
    term_program: str = ""
    term: str = ""
    is_tty: bool = False
    columns: int = 80
    rows: int = 24


def detect_terminal_capabilities() -> TerminalCapabilities:
    """通过环境变量和 terminfo 检测终端能力。"""
    term_program = os.environ.get("TERM_PROGRAM", "")
    term = os.environ.get("TERM", "")
    is_tty = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()

    columns = 80
    rows = 24
    if hasattr(sys.stdout, "columns") and sys.stdout.columns:
        columns = sys.stdout.columns
    if hasattr(sys.stdout, "rows") and sys.stdout.rows:
        rows = sys.stdout.rows

    # 检测 true color 支持
    true_color = _detect_true_color(term_program, term)

    # 检测同步输出支持
    synchronized_output = _detect_synchronized_output(term_program, term)

    # 检测扩展键支持
    kitty_keyboard = term_program in _EXTENDED_KEYS_TERMINALS
    modify_other_keys = kitty_keyboard

    # Alt screen 和鼠标追踪通常在 TTY 环境中可用
    alt_screen = is_tty
    mouse_tracking = is_tty

    return TerminalCapabilities(
        alt_screen=alt_screen,
        mouse_tracking=mouse_tracking,
        kitty_keyboard=kitty_keyboard,
        modify_other_keys=modify_other_keys,
        true_color=true_color,
        unicode=True,
        synchronized_output=synchronized_output,
        focus_reporting=is_tty,
        bracketed_paste=is_tty,
        term_program=term_program,
        term=term,
        is_tty=is_tty,
        columns=columns,
        rows=rows,
    )


def _detect_true_color(term_program: str, term: str) -> bool:
    """检测终端是否支持 true color (24-bit color)。"""
    # COLORTERM 环境变量是最可靠的检测方式
    colorterm = os.environ.get("COLORTERM", "")
    if colorterm in ("truecolor", "24bit"):
        return True

    # 已知支持 true color 的终端
    if term_program in (
        "iTerm.app",
        "WezTerm",
        "ghostty",
        "WarpTerminal",
        "vscode",
    ):
        return True

    # kitty
    if "kitty" in term or os.environ.get("KITTY_WINDOW_ID"):
        return True

    # Windows Terminal
    if os.environ.get("WT_SESSION"):
        return True

    return False


def _detect_synchronized_output(term_program: str, term: str) -> bool:
    """检测终端是否支持 DEC 2026 同步输出。"""
    # tmux 不实现 DEC 2026
    if os.environ.get("TMUX"):
        return False

    if term_program in _SYNC_OUTPUT_TERMINALS:
        return True

    # kitty
    if "kitty" in term or os.environ.get("KITTY_WINDOW_ID"):
        return True

    # Ghostty
    if term == "xterm-ghostty":
        return True

    # foot
    if term.startswith("foot"):
        return True

    # Alacritty
    if "alacritty" in term:
        return True

    # Zed
    if os.environ.get("ZED_TERM"):
        return True

    # Windows Terminal
    if os.environ.get("WT_SESSION"):
        return True

    # VTE >= 0.68
    vte_version = os.environ.get("VTE_VERSION", "")
    if vte_version:
        try:
            if int(vte_version) >= 6800:
                return True
        except ValueError:
            pass

    return False


# ---------------------------------------------------------------------------
# 终端操作函数
# ---------------------------------------------------------------------------

def enter_alt_screen() -> None:
    """进入替代屏幕缓冲区。"""
    write_ansi(ENTER_ALT_SCREEN)


def exit_alt_screen() -> None:
    """退出替代屏幕缓冲区。"""
    write_ansi(EXIT_ALT_SCREEN)


def enable_mouse_tracking() -> None:
    """启用鼠标追踪。"""
    write_ansi(ENABLE_MOUSE_TRACKING_ALL)


def disable_mouse_tracking() -> None:
    """禁用鼠标追踪。"""
    write_ansi(DISABLE_MOUSE_TRACKING_ALL)


def write_ansi(sequence: str) -> None:
    """安全写入 ANSI 序列到 stdout。"""
    try:
        if hasattr(sys.stdout, "write"):
            sys.stdout.write(sequence)
            if hasattr(sys.stdout, "flush"):
                sys.stdout.flush()
    except (IOError, OSError):
        pass


def cursor_move(dx: int, dy: int) -> str:
    """生成相对光标移动序列。"""
    parts = []
    if dy < 0:
        parts.append(f"{CSI}{-dy}A")  # CUU
    elif dy > 0:
        parts.append(f"{CSI}{dy}B")  # CUD
    if dx < 0:
        parts.append(f"{CSI}{-dx}D")  # CUB
    elif dx > 0:
        parts.append(f"{CSI}{dx}C")  # CUF
    return "".join(parts)


def cursor_position(row: int, col: int) -> str:
    """生成绝对光标定位序列 (1-indexed)。"""
    return f"{CSI}{row};{col}H"


def erase_lines(count: int) -> str:
    """生成擦除行序列。"""
    if count <= 0:
        return ""
    # 移动到行首，擦除当前行，然后向上擦除
    parts = [CSI + "2K"]  # 擦除当前行
    for _ in range(count - 1):
        parts.append(CSI + "A")  # 上移一行
        parts.append(CSI + "2K")  # 擦除该行
    parts.append("\r")  # 回到行首
    return "".join(parts)
