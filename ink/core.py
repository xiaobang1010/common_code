"""Ink 渲染引擎主类。

参考原始 TypeScript 实现: src/ink/ink.tsx

管理渲染生命周期，包括：
- 双缓冲 (front_frame / back_frame)
- 节流调度渲染 (throttle + 双缓冲)
- 全屏模式切换 (alt-screen)
- 终端状态清理
"""

from __future__ import annotations

import sys
import time
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .log_update import (
    Diff,
    Frame,
    FrameCursor,
    FrameViewport,
    LogUpdate,
    empty_frame,
    write_diff_to_terminal,
)
from .renderer import LayoutInfo, RenderNode, Renderer
from .screen import Screen
from .terminal import (
    BSU,
    CURSOR_HOME,
    ESU,
    ERASE_SCREEN,
    EXIT_ALT_SCREEN,
    HIDE_CURSOR,
    SHOW_CURSOR,
    TerminalCapabilities,
    detect_terminal_capabilities,
    enter_alt_screen,
    exit_alt_screen,
    write_ansi,
)


# 帧间隔 (约 60fps)
FRAME_INTERVAL_MS = 16
FRAME_INTERVAL_S = FRAME_INTERVAL_MS / 1000.0


# ---------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------

@dataclass
class InkOptions:
    """Ink 初始化选项。"""
    stdout: Any = None
    stdin: Any = None
    stderr: Any = None
    debug: bool = False
    exit_on_ctrl_c: bool = True
    patch_console: bool = False


# ---------------------------------------------------------------------------
# Ink
# ---------------------------------------------------------------------------

class Ink:
    """管理渲染生命周期的终端渲染引擎。

    核心功能：
    - 双缓冲：front_frame / back_frame 交替使用
    - 节流调度：schedule_render 使用 throttle 控制帧率
    - 全屏模式：enter_alternate_screen / exit_alternate_screen
    - 终端状态清理：cleanup 恢复终端原始状态
    """

    def __init__(self, options: Optional[InkOptions] = None) -> None:
        self._options = options or InkOptions()
        self._stdout = self._options.stdout or sys.stdout
        self._stdin = self._options.stdin or sys.stdin
        self._stderr = self._options.stderr or sys.stderr

        # 终端能力
        self._capabilities = detect_terminal_capabilities()
        self._terminal_columns = self._capabilities.columns
        self._terminal_rows = self._capabilities.rows

        # 双缓冲
        self._front_frame = empty_frame(self._terminal_rows, self._terminal_columns)
        self._back_frame = empty_frame(self._terminal_rows, self._terminal_columns)

        # 渲染器
        self._renderer = Renderer()

        # LogUpdate (帧差分)
        is_tty = self._capabilities.is_tty
        self._log = LogUpdate(stream=self._stdout, is_tty=is_tty)

        # 节流控制
        self._last_render_time: float = 0.0
        self._render_scheduled = False
        self._render_lock = threading.Lock()
        self._schedule_timer: Optional[threading.Timer] = None

        # 状态
        self._is_unmounted = False
        self._is_paused = False
        self._alt_screen_active = False
        self._prev_frame_contaminated = False

        # 当前渲染内容
        self._current_node: Optional[RenderNode] = None

    # -----------------------------------------------------------------------
    # 公共 API
    # -----------------------------------------------------------------------

    def render(self, content: Union[RenderNode, str, None]) -> None:
        """渲染内容到终端。

        Args:
            content: 可以是 RenderNode、字符串或 None
        """
        if self._is_unmounted:
            return

        # 将字符串转换为 RenderNode
        if isinstance(content, str):
            node = RenderNode(
                type="text",
                props={"children": content},
            )
            node.layout_info = LayoutInfo(
                x=0, y=0,
                width=self._terminal_columns,
                height=self._terminal_rows,
            )
        elif content is not None:
            node = content
        else:
            return

        self._current_node = node
        self._on_render()

    def schedule_render(self) -> None:
        """节流调度渲染。

        使用 throttle 机制控制帧率，避免过度渲染。
        如果距上次渲染不足 FRAME_INTERVAL_MS，则延迟调度。
        """
        if self._is_unmounted or self._is_paused:
            return

        with self._render_lock:
            if self._render_scheduled:
                return
            self._render_scheduled = True

        now = time.monotonic()
        elapsed = now - self._last_render_time
        delay = max(0, FRAME_INTERVAL_S - elapsed)

        if delay <= 0:
            # 立即渲染
            with self._render_lock:
                self._render_scheduled = False
            self._on_render()
        else:
            # 延迟调度
            if self._schedule_timer is not None:
                self._schedule_timer.cancel()
            self._schedule_timer = threading.Timer(
                delay, self._deferred_render
            )
            self._schedule_timer.daemon = True
            self._schedule_timer.start()

    def _deferred_render(self) -> None:
        """延迟渲染回调。"""
        with self._render_lock:
            self._render_scheduled = False
        self._on_render()

    def _on_render(self) -> None:
        """实际渲染逻辑。"""
        if self._is_unmounted or self._is_paused:
            return

        self._last_render_time = time.monotonic()

        # 获取终端尺寸
        self._update_terminal_size()

        # 创建新的帧
        next_frame = empty_frame(self._terminal_rows, self._terminal_columns)

        # 渲染组件树到 Screen
        if self._current_node is not None:
            self._renderer.render(self._current_node, next_frame.screen)

        # 计算差分
        prev_frame = self._front_frame
        if self._alt_screen_active:
            # Alt-screen: 锚定光标到 (0,0)
            prev_frame = Frame(
                screen=prev_frame.screen,
                viewport=prev_frame.viewport,
                cursor=FrameCursor(0, 0, False),
            )

        diff = self._log.render(prev_frame, next_frame, self._alt_screen_active)

        # 交换缓冲区
        self._back_frame = self._front_frame
        self._front_frame = next_frame

        # Alt-screen 前置 CSI H
        if self._alt_screen_active and diff:
            diff.insert(0, type(diff[0])(type="stdout", content=CURSOR_HOME))

        # 写入终端
        write_diff_to_terminal(self._stdout, diff)

    def enter_alternate_screen(self) -> None:
        """进入全屏模式 (alt-screen)。"""
        if self._alt_screen_active:
            return

        self._pause()
        write_ansi(
            HIDE_CURSOR +
            "\x1b[?1049h" +  # 进入 alt-screen
            "\x1b[2J" +      # 清屏
            CURSOR_HOME       # 光标归位
        )
        self._alt_screen_active = True
        self._reset_frames_for_alt_screen()
        self._resume()

    def exit_alternate_screen(self) -> None:
        """退出全屏模式。"""
        if not self._alt_screen_active:
            return

        write_ansi(
            SHOW_CURSOR +
            EXIT_ALT_SCREEN
        )
        self._alt_screen_active = False
        self._repaint()

    def cleanup(self) -> None:
        """清理终端状态。"""
        if self._is_unmounted:
            return

        # 取消待定渲染
        if self._schedule_timer is not None:
            self._schedule_timer.cancel()
            self._schedule_timer = None

        # 最后一帧渲染
        self._on_render()

        # 退出 alt-screen
        if self._alt_screen_active:
            write_ansi(EXIT_ALT_SCREEN)

        # 恢复终端模式
        write_ansi(SHOW_CURSOR)

        self._is_unmounted = True

    def pause(self) -> None:
        """暂停渲染。"""
        self._is_paused = True

    def resume(self) -> None:
        """恢复渲染。"""
        self._is_paused = False
        self._on_render()

    def force_redraw(self) -> None:
        """强制全量重绘。"""
        if not self._capabilities.is_tty or self._is_unmounted or self._is_paused:
            return
        write_ansi(ERASE_SCREEN + CURSOR_HOME)
        if self._alt_screen_active:
            self._reset_frames_for_alt_screen()
        else:
            self._repaint()
        self._on_render()

    # -----------------------------------------------------------------------
    # 内部方法
    # -----------------------------------------------------------------------

    def _pause(self) -> None:
        """内部暂停。"""
        self._is_paused = True

    def _resume(self) -> None:
        """内部恢复。"""
        self._is_paused = False

    def _update_terminal_size(self) -> None:
        """更新终端尺寸。"""
        if hasattr(self._stdout, "columns") and self._stdout.columns:
            self._terminal_columns = self._stdout.columns
        if hasattr(self._stdout, "rows") and self._stdout.rows:
            self._terminal_rows = self._stdout.rows

    def _reset_frames_for_alt_screen(self) -> None:
        """重置帧缓冲区为全尺寸空白屏幕。

        在 alt-screen 模式下，prev.screen.height 必须等于 terminalRows，
        否则 log-update 的 heightDelta > 0 会导致滚动。
        """
        rows = self._terminal_rows
        cols = self._terminal_columns
        self._front_frame = Frame(
            screen=Screen(cols, rows),
            viewport=FrameViewport(cols, rows + 1),
            cursor=FrameCursor(0, 0, True),
        )
        self._back_frame = Frame(
            screen=Screen(cols, rows),
            viewport=FrameViewport(cols, rows + 1),
            cursor=FrameCursor(0, 0, True),
        )
        self._log.reset()
        self._prev_frame_contaminated = True

    def _repaint(self) -> None:
        """重置帧缓冲区以触发全量重绘。"""
        self._front_frame = empty_frame(self._terminal_rows, self._terminal_columns)
        self._back_frame = empty_frame(self._terminal_rows, self._terminal_columns)
        self._log.reset()


# ---------------------------------------------------------------------------
# 测试块
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import io

    print("=== Ink 渲染引擎测试 ===\n")

    # 1. 测试终端能力检测
    print("--- 终端能力检测 ---")
    caps = detect_terminal_capabilities()
    print(f"  is_tty: {caps.is_tty}")
    print(f"  columns: {caps.columns}, rows: {caps.rows}")
    print(f"  true_color: {caps.true_color}")
    print(f"  synchronized_output: {caps.synchronized_output}")
    print(f"  term_program: {caps.term_program!r}")
    print(f"  term: {caps.term!r}")

    # 2. 测试简单文本渲染
    print("\n--- 简单文本渲染 ---")
    # 使用 StringIO 模拟 stdout 以避免影响真实终端
    buffer = io.StringIO()
    options = InkOptions(stdout=buffer)
    ink = Ink(options)

    # 渲染 "Hello, World!"
    ink.render("Hello, World!")
    output = buffer.getvalue()
    print(f"  渲染输出: {output!r}")

    # 3. 测试 Screen 缓冲区
    print("\n--- Screen 缓冲区 ---")
    screen = Screen(20, 5)
    screen.set_cell(0, 0, "H")
    screen.set_cell(1, 0, "e")
    screen.set_cell(2, 0, "l")
    screen.set_cell(3, 0, "l")
    screen.set_cell(4, 0, "o")
    rendered = screen.to_string()
    print(f"  Screen 内容: {rendered!r}")

    # 4. 测试带样式的渲染
    print("\n--- 带样式的渲染 ---")
    buffer2 = io.StringIO()
    options2 = InkOptions(stdout=buffer2)
    ink2 = Ink(options2)

    node = RenderNode(
        type="text",
        props={"children": "Styled Text", "fg": "green", "bold": True},
        layout_info=LayoutInfo(0, 0, 80, 24),
    )
    ink2.render(node)
    output2 = buffer2.getvalue()
    print(f"  带样式渲染输出: {output2!r}")

    # 5. 测试帧差分
    print("\n--- 帧差分 ---")
    log = LogUpdate(stream=io.StringIO(), is_tty=True)

    prev = empty_frame(24, 80)
    prev.screen.set_cell(0, 0, "H")
    prev.screen.set_cell(1, 0, "i")

    next_frame = empty_frame(24, 80)
    next_frame.screen.set_cell(0, 0, "H")
    next_frame.screen.set_cell(1, 0, "o")  # 变化: i -> o

    diff = log.render(prev, next_frame)
    print(f"  差分 Patch 数量: {len(diff)}")
    for i, patch in enumerate(diff):
        print(f"    Patch[{i}]: type={patch.type}, "
              f"content={getattr(patch, 'content', getattr(patch, 'str', ''))!r}")

    # 6. 测试 alt-screen (仅在 TTY 环境中)
    print("\n--- Alt-Screen 测试 ---")
    if caps.is_tty:
        print("  检测到 TTY 环境，测试 alt-screen 进入/退出...")
        ink_tty = Ink()
        ink_tty.enter_alternate_screen()
        ink_tty.render("Alt-Screen Mode!")
        time.sleep(0.5)
        ink_tty.exit_alternate_screen()
        ink_tty.cleanup()
        print("  Alt-screen 测试完成")
    else:
        print("  非 TTY 环境，跳过 alt-screen 测试")

    # 7. 测试 Screen resize
    print("\n--- Screen Resize ---")
    screen3 = Screen(10, 3)
    screen3.set_cell(0, 0, "A")
    screen3.set_cell(5, 2, "B")
    screen3.resize(15, 5)
    cell_a = screen3.get_cell(0, 0)
    cell_b = screen3.get_cell(5, 2)
    print(f"  Resize 后 (0,0)={cell_a.char if cell_a else 'None'}")
    print(f"  Resize 后 (5,2)={cell_b.char if cell_b else 'None'}")
    print(f"  新尺寸: {screen3.width}x{screen3.height}")

    print("\n=== 测试完成 ===")
