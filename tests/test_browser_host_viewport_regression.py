"""内置浏览器宿主视口与超时预算回归测试。

背景：宿主侧曾在 webContents（guest）上调用 getSize()——那是 BrowserWindow 的方法，
WebContents 并不存在——导致经过 summaryOf/getState 的所有命令（list/newTab/
activateTab/getState）必返回 execution_error，智能体浏览器操作全量失败。

三层守卫：
① node 跑 plain-node 协议单测（假 webContents 刻意不提供 getSize，回归即红）；
② 静态扫描 electron/browser/ 断言 .getSize( 调用清零；
③ 常量预算断言：TAB_CREATE(5000) < REQUEST(10000) < runner execute(15000)，
   保证渲染握手失败以明确业务错误呈现、慢页面导航不被 HTTP 层抢先掐断。
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BROWSER_DIR = PROJECT_ROOT / "electron" / "browser"
NODE_TEST = Path(__file__).resolve().parent / "electron" / "browser_viewport_units.test.cjs"
RUNNER = (
    PROJECT_ROOT
    / "startup"
    / "plugins"
    / "bundled"
    / "browser-use"
    / "skills"
    / "control-browser"
    / "scripts"
    / "browser-runner.mjs"
)


class TestNodeUnits:
    """①node 协议单测。"""

    def test_node_units_pass(self) -> None:
        # PATH 无 node 时跳过（非失败），不照抄 test_browser_bridge_env.py——它未做此 skip
        if shutil.which("node") is None:
            pytest.skip("node 不可用")
        result = subprocess.run(
            ["node", str(NODE_TEST)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, f"node 单测失败：\n{result.stdout}\n{result.stderr}"


class TestStaticGuards:
    """②③静态守卫。"""

    def test_no_get_size_call_in_browser_modules(self) -> None:
        offenders: list[str] = []
        for js in sorted(BROWSER_DIR.glob("*.js")):
            for lineno, line in enumerate(js.read_text(encoding="utf-8").splitlines(), 1):
                # 行内代码部分才计数，注释允许提及该坑
                code = line.split("//", 1)[0]
                for forbidden in (".getSize(", ".getBounds("):
                    if forbidden in code:
                        offenders.append(f"{js.name}:{lineno}: {line.strip()}")
        assert not offenders, (
            "electron/browser/ 内的对象是 webContents（guest）：getSize/getBounds 均属 "
            "BrowserWindow，guest 视口只能经 CDP layoutMetrics 读取；违规位置：" + " | ".join(offenders)
        )

    def test_timeout_budget_relationship(self) -> None:
        server_src = (BROWSER_DIR / "server.js").read_text(encoding="utf-8")
        tabs_src = (BROWSER_DIR / "tabs.js").read_text(encoding="utf-8")
        runner_src = RUNNER.read_text(encoding="utf-8")

        m = re.search(r"REQUEST_TIMEOUT_MS\s*=\s*(\d+)", server_src)
        assert m, "server.js 未找到 REQUEST_TIMEOUT_MS 常量"
        request_timeout = int(m.group(1))

        m = re.search(r"TAB_CREATE_TIMEOUT_MS\s*=\s*(\d+)", tabs_src)
        assert m, "tabs.js 未找到 TAB_CREATE_TIMEOUT_MS 常量"
        tab_create_timeout = int(m.group(1))

        # runner 有多处 AbortSignal.timeout（连通性探测 2000 / execute 传输 15000），
        # 传输预算取最大值
        budgets = [int(x) for x in re.findall(r"AbortSignal\.timeout\((\d+)\)", runner_src)]
        assert budgets, "browser-runner.mjs 未找到传输预算"
        runner_budget = max(budgets)

        # 渲染握手超时须先于 HTTP 层到达（明确业务错误），HTTP 层再先于 runner 断连
        assert tab_create_timeout < request_timeout, (
            f"REQUEST_TIMEOUT_MS({request_timeout}) 必须大于 "
            f"TAB_CREATE_TIMEOUT_MS({tab_create_timeout})"
        )
        assert request_timeout < runner_budget, (
            f"REQUEST_TIMEOUT_MS({request_timeout}) 必须小于 "
            f"runner 传输预算({runner_budget})"
        )
