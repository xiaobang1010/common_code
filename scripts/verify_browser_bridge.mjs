// 内置浏览器控制桥冒烟脚本（项目根 scripts/，与技能目录下 scripts/ 无关）。
// 用法：桌面端运行下执行 `node scripts/verify_browser_bridge.mjs`。
// 链路：桥接文件 → 描述符 list() → 标签 list/newTab/navigate/domSnapshot/screenshot/close，
// 按三层协议契约断言响应结构（描述符形状、负载键随命令而定、tabId 字段）。
import { readFileSync } from "node:fs";

function assert(cond, msg) {
  if (!cond) {
    console.error(`FAIL: ${msg}`);
    process.exit(1);
  }
  console.log(`ok: ${msg}`);
}

async function main() {
  const bridgePath = process.env.COMMON_CODE_BROWSER_BRIDGE_FILE;
  assert(bridgePath, "COMMON_CODE_BROWSER_BRIDGE_FILE 已注入");
  let info;
  try {
    info = JSON.parse(readFileSync(bridgePath, "utf8"));
  } catch (e) {
    console.error(`FAIL: 桥接文件不可读（${bridgePath}）：${e.message}`);
    process.exit(1);
  }
  assert(typeof info.port === "number" && typeof info.token === "string", "桥接文件含 port/token");

  const base = `http://127.0.0.1:${info.port}`;
  const headers = { Authorization: `Bearer ${info.token}`, "Content-Type": "application/json" };

  // 鉴权负例：错误令牌必须 401
  const bad = await fetch(`${base}/browsers`, { headers: { Authorization: "Bearer wrong" } });
  assert(bad.status === 401, "错误令牌返回 401");

  // 描述符契约
  const desc = (await (await fetch(`${base}/browsers`, { headers })).json()).browsers;
  assert(Array.isArray(desc) && desc.length === 1, "list() 返回单条描述符");
  const d = desc[0];
  assert(d.type === "iab" && typeof d.id === "string" && typeof d.generation === "number", "描述符含 id/generation/type=iab");
  assert(Array.isArray(d.capabilities.browser) && d.capabilities.browser.some((c) => c.id === "visibility"), "browser 能力含 visibility");

  const exec = async (command) => {
    const res = await fetch(`${base}/execute`, {
      method: "POST",
      headers,
      body: JSON.stringify({ browserId: d.id, browserGeneration: d.generation, command }),
    });
    return res.json();
  };

  // 标签链路
  const created = await exec({ method: "newTab" });
  assert(created.ok && created.tab && typeof created.tab.tabId === "string", "newTab 返回 {ok,tab.tabId}");
  const tabId = created.tab.tabId;
  assert(created.tab.viewport && typeof created.tab.viewport.width === "number", "tab 摘要含 viewport.width");

  const nav = await exec({ method: "navigate", tabId, url: "https://example.com/" });
  assert(nav.ok, "navigate 返回 {ok}");

  await new Promise((r) => setTimeout(r, 2500));
  const load = await exec({ method: "playwright", tabId, action: { name: "waitForLoadState", state: "domcontentloaded" } });
  assert(load.ok, "waitForLoadState 返回 {ok}");

  const snap = await exec({ method: "playwright", tabId, action: { name: "domSnapshot" } });
  assert(snap.ok && typeof snap.value === "string" && snap.value.includes("RootWebArea"), "domSnapshot 返回 {ok,value} 含 RootWebArea");

  const shot = await exec({ method: "screenshot", tabId });
  assert(shot.ok && shot.image && typeof shot.image.base64 === "string" && shot.image.base64.length > 100, "screenshot 返回 {ok,image.base64}");

  // 未支持命令的结构化拒绝
  const unsup = await exec({ method: "recordingStart" });
  assert(!unsup.ok && unsup.error && unsup.error.code === "unsupported", "recordingStart 返回 {ok:false,error.code=unsupported}");

  const closed = await exec({ method: "close", tabId });
  assert(closed.ok, "close 返回 {ok}");
  const list = await exec({ method: "list" });
  assert(list.ok && Array.isArray(list.tabs) && !list.tabs.some((t) => t.tabId === tabId), "close 后 list 不再含该标签");

  console.log("\n浏览器桥冒烟全部通过");
}

main().catch((e) => {
  console.error(`FAIL: ${e.message}`);
  process.exit(1);
});
