"""内置浏览器桥接分发与技能执行器错误路径测试。

覆盖：launch.py 把桥接文件路径环境变量注入后端与 Electron 两个子进程环境；
技能侧 browser-runner 在桥接文件缺失/端口不可连时按「宿主未运行」回退
（非零退出码 + 可读错误），保证纯 CLI 环境下技能不伪造浏览器结果。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LAUNCH_PY = PROJECT_ROOT / "launch.py"
RUNNER = (
    PROJECT_ROOT
    / "startup" / "plugins" / "bundled" / "browser-use" / "skills" / "control-browser"
    / "scripts" / "browser-runner.mjs"
)

BRIDGE_ENV = "COMMON_CODE_BROWSER_BRIDGE_FILE"


def _launch_source() -> str:
    return LAUNCH_PY.read_text(encoding="utf-8")


class TestLaunchBridgeEnv:
    """launch.py 环境变量分发。"""

    def test_constant_defined(self) -> None:
        src = _launch_source()
        assert f'BROWSER_BRIDGE_ENV = "{BRIDGE_ENV}"' in src
        # 桥接路径落在用户目录，与工作区无关
        assert re.search(r'Path\.home\(\)\s*/\s*"\.common-code"\s*/\s*"browser-bridge\.json"', src)

    def test_injected_into_both_subprocess_envs(self) -> None:
        src = _launch_source()
        # 后端与 Electron 两处 env 构造都注入桥接变量（各 1 处，共 2 处）
        assert src.count("BROWSER_BRIDGE_ENV: str(BROWSER_BRIDGE_FILE)") == 2


def _run_node(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["node", *args],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )


@pytest.fixture()
def js_task(tmp_path: Path) -> Path:
    """任意合法 JS 片段：错误路径断言只关心它跑不到执行阶段。"""
    task = tmp_path / "task.js"
    task.write_text("nodeRepl.write('should-not-run')", encoding="utf-8")
    return task


class TestRunnerUnavailable:
    """桥接缺失/陈旧时 runner 的宿主未运行回退。"""

    def test_missing_bridge_file(self, js_task: Path, tmp_path: Path) -> None:
        env = {"PATH": os.environ.get("PATH", ""),
               BRIDGE_ENV: str(tmp_path / "no-such-dir" / "browser-bridge.json")}
        r = _run_node([str(RUNNER), str(js_task)], env)
        assert r.returncode != 0
        assert "内置浏览器不可用" in r.stderr

    def test_stale_bridge_port(self, js_task: Path, tmp_path: Path) -> None:
        # 端口 1 必然无监听：模拟 Electron 异常退出后残留的陈旧桥接
        bridge = tmp_path / "browser-bridge.json"
        bridge.write_text(json.dumps({"port": 1, "token": "stale"}), encoding="utf-8")
        env = {"PATH": os.environ.get("PATH", ""),
               BRIDGE_ENV: str(bridge)}
        r = _run_node([str(RUNNER), str(js_task)], env)
        assert r.returncode != 0
        assert "内置浏览器不可用" in r.stderr

    def test_usage_error_without_file_arg(self) -> None:
        env = {"PATH": os.environ.get("PATH", ""),
               BRIDGE_ENV: "unused"}
        r = _run_node([str(RUNNER)], env)
        assert r.returncode != 0
        assert "用法" in r.stderr
