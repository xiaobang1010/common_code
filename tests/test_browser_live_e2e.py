"""桌面端在跑时的内置浏览器全链路冒烟（技能 runner → 桥接 → 控制服务 → guest webview）。

前置三条件任一不满足即 skip（桌面端没跑时不阻塞 CI）：
① node 不在 PATH；② 桥接文件缺失（COMMON_CODE_BROWSER_BRIDGE_FILE 未设且默认路径无文件，
或文件不可解析）；③ GET /browsers 探测（2s 超时，与 runner 连通性探测口径一致）无响应。

正向链路经真实技能执行器跑一次完整浏览器操作：
tabs.new → goto 本地测试页 → waitForLoadState → domSnapshot 断言标记 → close。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
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
BRIDGE_ENV = "COMMON_CODE_BROWSER_BRIDGE_FILE"
BRIDGE_DEFAULT = Path.home() / ".common-code" / "browser-bridge.json"

PAGE_TITLE = "内置浏览器冒烟页"
PAGE_MARKER = "LIVE-E2E-MARKER"


def _resolve_bridge() -> dict | None:
    """按 runner 同口径解析桥接；不可用返回 None（调用方 skip）。"""
    path = Path(os.environ.get(BRIDGE_ENV) or BRIDGE_DEFAULT)
    if not path.is_file():
        return None
    try:
        info = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    port, token = info.get("port"), info.get("token")
    if not isinstance(port, int) or not token:
        return None
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/browsers",
            headers={"Authorization": f"Bearer {token}"},
        )
        with urllib.request.urlopen(req, timeout=2) as res:
            data = json.loads(res.read().decode("utf-8", errors="replace"))
        if not data.get("ok") or not data.get("browsers"):
            return None
    except OSError:
        return None
    return {"port": port, "token": token, "path": str(path)}


@pytest.fixture(scope="module")
def bridge() -> dict:
    if shutil.which("node") is None:
        pytest.skip("node 不可用")
    info = _resolve_bridge()
    if info is None:
        pytest.skip("桌面端未运行")
    return info


@pytest.fixture()
def local_page():
    """本地测试页：固定标题与快照标记文本。"""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            body = (
                f"<!doctype html><html><head><title>{PAGE_TITLE}</title></head>"
                f"<body><h1>{PAGE_MARKER}</h1></body></html>"
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):  # 静音访问日志
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}/"
    finally:
        srv.shutdown()
        srv.server_close()


class TestBrowserLiveE2E:
    def test_full_flow_through_skill_runner(self, bridge, local_page, tmp_path) -> None:
        url = local_page
        task_js = tmp_path / "live-e2e-task.js"
        task_js.write_text(
            f"""
const browser = await agent.browsers.getDefault();
const tab = await browser.tabs.new();
await tab.goto("{url}");
await tab.playwright.waitForLoadState({{ state: "domcontentloaded" }});
const snap = await tab.playwright.domSnapshot();
const title = await tab.title();
nodeRepl.write({{ markerInSnapshot: snap.includes("{PAGE_MARKER}"), title }});
await tab.close();
nodeRepl.write("LIVE-E2E-DONE");
""",
            encoding="utf-8",
        )

        env = {**os.environ, BRIDGE_ENV: bridge["path"]}
        result = subprocess.run(
            ["node", str(RUNNER), str(task_js)],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=PROJECT_ROOT,
            env=env,
        )
        output = result.stdout + result.stderr
        assert result.returncode == 0, f"runner 失败（exit={result.returncode}）：\n{output}"
        assert '"markerInSnapshot": true' in output, f"快照未含测试标记：\n{output}"
        assert PAGE_TITLE in output, f"页面标题不符：\n{output}"
        assert "LIVE-E2E-DONE" in output, f"链路未走完：\n{output}"
