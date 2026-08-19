"""终端控制台模式启动器。

launch.py 托管全生命周期：构建前端 → 派生后端 → 解析端口 JSON → 派生 Electron
（env 传端口）→ 等待 → 退出清理。后端与 Electron 都是本进程的直接子进程，
日志直出终端；退出时按进程树清理（Windows taskkill /T、Unix killpg），
保证 uv 链下的 python 孙进程不残留。

用法：
    python launch.py             # 默认先构建前端再启动
    python launch.py --no-build  # 跳过前端构建，直接用已有产物启动
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional, Sequence

# 项目根目录（launch.py 所在目录），后端与前端都以它为工作目录
PROJECT_ROOT = Path(__file__).resolve().parent
FRONTEND_DIR = PROJECT_ROOT / "frontend"
ELECTRON_DIR = PROJECT_ROOT / "electron"

# 传给 Electron 的后端端口环境变量名（与 electron/main.js 约定）
BACKEND_PORT_ENV = "COMMON_CODE_BACKEND_PORT"
# 端口握手超时（秒）：超时判定后端启动失败
PORT_HANDSHAKE_TIMEOUT = 60.0
# TCP 就绪探测超时（秒）
PROBE_TIMEOUT = 5.0
# Unix 上 SIGTERM 后的宽限时间（秒），超过升级为 SIGKILL
TERM_GRACE_SECONDS = 3.0


# Windows 上进程被 Ctrl+C 终止的退出码（STATUS_CONTROL_C_EXIT）；
# 同控制台的后端会随 Ctrl+C 连带退出，监控线程应视为「随 Ctrl+C 关闭」而非崩溃
STATUS_CONTROL_C_EXIT = 0xC000013A


class StartupError(Exception):
    """启动阶段失败（构建 / 端口握手 / 就绪探测），携带给用户看的原因。"""


class Launcher:
    """启动器：持有后端与 Electron 进程，统一管理生命周期与清理。

    四条退出路径（Electron 退出、Ctrl+C、后端崩溃、控制台关闭）都汇聚到
    cleanup() 单一入口，靠锁 + 已清理标志保证幂等。
    """

    def __init__(self) -> None:
        self.backend: Optional[subprocess.Popen[bytes]] = None
        self.electron: Optional[subprocess.Popen[bytes]] = None
        # 后端退出码（崩溃监控线程写入），用于决定 launch.py 自身退出码
        self.backend_exit_code: Optional[int] = None
        # cleanup 幂等状态机
        self._cleanup_lock = threading.Lock()
        self._cleaned = False
        # cleanup 完成事件：主线程退出前等它，防止 daemon 线程里的清理被解释器退出截断
        self._cleanup_done = threading.Event()
        # 端口握手：stdout 转发线程解析到端口 JSON 后置位
        self._port_found = threading.Event()
        self._port: Optional[int] = None
        # 后端 stderr 累积缓冲（只留最近若干行），启动失败时打印现场
        self._stderr_tail: list[str] = []
        # Windows 控制台事件处理函数引用，持有防被垃圾回收
        self._console_handler_ref: object = None

    # ------------------------------------------------------------------
    # 前端构建
    # ------------------------------------------------------------------

    def build_frontend(self) -> None:
        """构建前端。失败抛 StartupError，调用方中止启动（不派生任何子进程）。"""
        # Windows 上 npm 只有 npm.cmd，无 shell 派生必须先解析全路径
        npm = shutil.which("npm.cmd") if sys.platform == "win32" else shutil.which("npm")
        if npm is None:
            raise StartupError("找不到 npm，请确认 Node.js 已安装并在 PATH 中")
        print("[launch] 构建前端 ...")
        # 输出直接继承终端，构建日志实时可见
        result = subprocess.run([npm, "run", "build"], cwd=FRONTEND_DIR)
        if result.returncode != 0:
            raise StartupError(f"前端构建失败（退出码 {result.returncode}），已中止启动")
        print("[launch] 前端构建完成")

    # ------------------------------------------------------------------
    # 后端
    # ------------------------------------------------------------------

    def start_backend(self) -> int:
        """派生后端并完成端口握手与 TCP 就绪探测，返回后端端口。

        失败（uv 缺失 / 提前退出 / 握手超时 / 探测不通）抛 StartupError，
        由 main 统一走 cleanup 清理。
        """
        uv = shutil.which("uv")
        if uv is None:
            raise StartupError("找不到 uv，请先安装 uv 并确认其在 PATH 中")
        env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
        # 无 shell 派生：Popen 的 pid 就是 uv 进程本身，进程树根明确，
        # kill_tree（taskkill /T / killpg）才能收干净 uv 链下的 python 孙进程
        self.backend = subprocess.Popen(
            [uv, "run", "python", "-m", "server"],
            cwd=PROJECT_ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=(sys.platform != "win32"),
        )
        # stdout/stderr 从派生起就由 daemon 线程排空转发，
        # 防止管道缓冲（~64KB）写满后端 sys.stdout.write 阻塞卡死
        threading.Thread(target=self._pump_stdout, daemon=True).start()
        threading.Thread(target=self._pump_stderr, daemon=True).start()

        # 端口握手：等 stdout 里出现 {"port": N}，同时盯后端是否提前退出
        deadline = time.monotonic() + PORT_HANDSHAKE_TIMEOUT
        while not self._port_found.wait(timeout=0.1):
            if self.backend.poll() is not None:
                raise StartupError(
                    f"后端启动即退出（退出码 {self.backend.returncode}）\n{self._stderr_text()}"
                )
            if time.monotonic() > deadline:
                raise StartupError(
                    f"等待后端端口握手超时（{PORT_HANDSHAKE_TIMEOUT:.0f}s）\n{self._stderr_text()}"
                )
        port = self._port
        assert port is not None

        # ready 二次确认：端口 JSON 本应在 uvicorn started 之后输出，
        # TCP 探测做兜底，防「端口给了但服务实际没起来」
        if not self._probe_port(port):
            raise StartupError(
                f"后端端口 {port} 无法连通（TCP 探测失败）\n{self._stderr_text()}"
            )
        print(f"[launch] 后端就绪，端口 {port}")
        return port

    def _pump_stdout(self) -> None:
        """后端 stdout 转发线程：逐行转发到终端，同时在行内解析端口 JSON。

        端口解析复用同一个 reader（不另开线程），避免两条 reader 互抢数据。
        """
        assert self.backend is not None and self.backend.stdout is not None
        for raw in iter(self.backend.stdout.readline, b""):
            line = raw.decode("utf-8", errors="replace").rstrip("\n")
            if line:
                print(line)
            # 端口握手：跳过非 JSON 的普通日志行，读到含 port 字段的 JSON 就置位
            if self._port is None:
                try:
                    parsed = json.loads(line)
                except ValueError:
                    continue
                if isinstance(parsed, dict) and isinstance(parsed.get("port"), int):
                    self._port = parsed["port"]
                    self._port_found.set()

    def _pump_stderr(self) -> None:
        """后端 stderr 转发线程：转发到终端，同时累积最近若干行供失败时打印现场。"""
        assert self.backend is not None and self.backend.stderr is not None
        for raw in iter(self.backend.stderr.readline, b""):
            line = raw.decode("utf-8", errors="replace").rstrip("\n")
            if line:
                print(line, file=sys.stderr)
            self._stderr_tail.append(line)
            if len(self._stderr_tail) > 200:
                self._stderr_tail.pop(0)

    def _stderr_text(self) -> str:
        """最近累积的后端 stderr，供启动失败时打印现场。"""
        return "\n".join(self._stderr_tail[-50:])

    @staticmethod
    def _probe_port(port: int) -> bool:
        """TCP 探测后端是否真的在监听 127.0.0.1:<port>。"""
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=PROBE_TIMEOUT):
                return True
        except OSError:
            return False

    # ------------------------------------------------------------------
    # Electron
    # ------------------------------------------------------------------

    def start_electron(self, port: int) -> None:
        """派生 Electron，通过环境变量把后端端口递过去。"""
        # Windows 上 npx 只有 npx.cmd，无 shell 派生必须先解析全路径，
        # 这样 Popen 的 pid 就是进程树根，taskkill /T 才收得干净 npx→node→electron 链
        npx = shutil.which("npx.cmd") if sys.platform == "win32" else shutil.which("npx")
        if npx is None:
            raise StartupError("找不到 npx，请确认 Node.js 已安装并在 PATH 中")
        env = {**os.environ, BACKEND_PORT_ENV: str(port)}
        # stdout/stderr 不接管，直接继承终端
        self.electron = subprocess.Popen(
            [npx, "electron", "."],
            cwd=ELECTRON_DIR,
            env=env,
            start_new_session=(sys.platform != "win32"),
        )

    # ------------------------------------------------------------------
    # 清理与监控
    # ------------------------------------------------------------------

    @staticmethod
    def kill_tree(pid: int) -> None:
        """按进程树杀进程。

        Windows 用 taskkill /PID <pid> /T /F（/T 覆盖整棵树，含 uv 链的
        python 孙进程——Popen.kill() 只杀直接子进程，正是残留的根因）；
        Unix 先 SIGTERM、宽限后 SIGKILL。进程已退出时静默返回。
        """
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            return
        try:
            os.killpg(pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + TERM_GRACE_SECONDS
        while time.monotonic() < deadline:
            try:
                # 信号 0 只探测进程组是否存在，不实际发信号
                os.killpg(pid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.1)
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    def cleanup(self) -> None:
        """统一清理入口：杀掉 Electron 与后端的整棵进程树。

        幂等：锁 + 已清理标志，Electron 退出 / Ctrl+C / 后端崩溃 /
        控制台关闭四条路径并发触发时只实际执行一次。
        """
        with self._cleanup_lock:
            if self._cleaned:
                return
            self._cleaned = True
        try:
            for proc, name in ((self.electron, "Electron"), (self.backend, "后端")):
                if proc is None:
                    # 启动早期失败时进程还没派生，无需清理
                    continue
                if proc.poll() is None:
                    self.kill_tree(proc.pid)
                    try:
                        proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        # 树已杀但 wait 超时属异常情况，不阻断其余清理
                        print(f"[launch] 等待 {name} 退出超时", file=sys.stderr)
                print(f"[launch] {name} 已退出")
        finally:
            self._cleanup_done.set()

    def wait_cleanup_done(self) -> None:
        """等待 cleanup 真正执行完毕（cleanup 可能由其他线程触发）。"""
        self._cleanup_done.wait(timeout=30)

    def start_backend_monitor(self) -> None:
        """启动后端崩溃监控线程（防「假活」窗口）。"""
        threading.Thread(target=self._monitor_backend, daemon=True).start()

    def _monitor_backend(self) -> None:
        """后端崩溃监控：后端先于 Electron 退出时，主动杀掉 Electron。

        否则静态页面还挂着但 API 全失效，用户面对的是假活窗口。
        cleanup 杀掉 Electron 后，主线程的 electron.wait() 自然返回。
        """
        assert self.backend is not None
        code = self.backend.wait()
        # cleanup 已在进行（正常关窗流程里是我们自己杀的后端），不算崩溃
        if self._cleaned:
            return
        # Ctrl+C 时同控制台的后端连带退出（Unix 上是 SIGINT，returncode=-2），
        # 主线程会走 KeyboardInterrupt 路径统一清理，这里不按崩溃处理
        if code in (STATUS_CONTROL_C_EXIT, -2):
            return
        self.backend_exit_code = code
        print(f"\n[launch] 后端进程退出（退出码 {code}），正在关闭 Electron ...", file=sys.stderr)
        self.cleanup()

    def install_console_handler(self) -> None:
        """Windows：注册控制台事件处理，覆盖 Ctrl+C / 关闭终端窗口等场景。

        直接关终端窗口时系统只发 CTRL_CLOSE_EVENT 给控制台进程，
        不注册处理函数的话 Electron（GUI）和后端（无控制台）都不会被通知，
        必然残留。非 Windows 平台 no-op（Ctrl+C 走 KeyboardInterrupt）。
        """
        if sys.platform != "win32":
            return
        import ctypes

        from ctypes import wintypes

        def _on_console_ctrl(event_type: int) -> bool:
            # Ctrl+C（事件 0）返回 False，交给 Python 自己的
            # KeyboardInterrupt 机制（主线程 except 路径，成熟可靠）
            if event_type == 0:
                return False
            # Ctrl+Break / 关闭终端 / 注销 / 关机：没有 Python 兜底，
            # 必须在 handler 里直接清理。处理函数返回前系统会等一小段时间，
            # 够 taskkill 完成整树清理
            self.cleanup()
            return True

        handler_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)
        # 持有引用防被垃圾回收导致回调失效
        self._console_handler_ref = handler_type(_on_console_ctrl)
        ctypes.windll.kernel32.SetConsoleCtrlHandler(self._console_handler_ref, True)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="终端控制台模式启动 common_code")
    parser.add_argument(
        "--no-build",
        action="store_true",
        help="跳过前端构建，直接用已有产物启动（快速重启）",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """主流程：构建 → 后端 → Electron → 等待 → 清理，返回进程退出码。"""
    # stdout 被重定向成管道时 Python 默认按块缓冲，会导致日志乱序，强制按行刷出
    sys.stdout.reconfigure(line_buffering=True)
    args = parse_args(argv)
    launcher = Launcher()
    try:
        if args.no_build:
            print("[launch] 跳过前端构建（--no-build）")
        else:
            launcher.build_frontend()

        port = launcher.start_backend()
        launcher.start_electron(port)
        # Windows 关终端 / Ctrl+C 都走控制台事件进统一 cleanup
        launcher.install_console_handler()
        launcher.start_backend_monitor()

        # 主线程等待 Electron：正常关窗、或后端崩溃被监控线程杀掉，都会让这里返回
        assert launcher.electron is not None
        launcher.electron.wait()
        launcher.cleanup()
        launcher.wait_cleanup_done()

        # 后端异常退出时，launch.py 以非 0 码退出
        if launcher.backend_exit_code is not None and launcher.backend_exit_code != 0:
            return launcher.backend_exit_code
        return 0
    except KeyboardInterrupt:
        print("\n[launch] 收到 Ctrl+C，正在退出 ...")
        launcher.cleanup()
        launcher.wait_cleanup_done()
        return 130
    except StartupError as exc:
        print(f"[launch] 启动失败：{exc}", file=sys.stderr)
        launcher.cleanup()
        launcher.wait_cleanup_done()
        return 1


if __name__ == "__main__":
    sys.exit(main())
