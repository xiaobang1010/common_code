'use strict';
// 内置浏览器宿主协议层单元测试（plain node，无需 Electron 二进制）。
// 背景：宿主侧曾在 webContents（guest）上调用 getSize()/getBounds()——两者都属
// BrowserWindow，WebContents 均不提供——导致经 tab 摘要的命令（list/newTab/
// activateTab/getState）必抛 TypeError，智能体浏览器操作全量失败。修复后视口
// 唯一来源是注入的 CDP 页面指标读取器（main.js 接 CdpExecutor.layoutMetrics）。
// 手法：require.cache 注入假 'electron' 模块；假 guest webContents 只暴露
// isDestroyed/getURL/getTitle/on/close——既不提供 getSize 也不提供 getBounds，
// 任何回归重新用这两者取尺寸都会在此立刻 TypeError。
// 运行：node tests/electron/browser_viewport_units.test.cjs（退出码 0 即通过）

const assert = require('node:assert');
const path = require('node:path');

const electronDir = path.resolve(__dirname, '..', '..', 'electron');

// ---------- 假 electron 模块 ----------
// ipc 通道监听表与 guest 注册表：供用例模拟渲染侧回报 / 控制 guest 对象
const ipcHandlers = new Map();
const guestRegistry = new Map();

const fakeElectron = {
  app: { on: () => {} },
  ipcMain: {
    on: (channel, fn) => ipcHandlers.set(channel, fn),
  },
  webContents: {
    fromId: (id) => guestRegistry.get(id) ?? null,
  },
};

// 注入：必须在 require tabs.js/server.js 之前占据 'electron' 的解析结果缓存
const electronKey = require.resolve('electron', { paths: [electronDir] });
require.cache[electronKey] = {
  id: electronKey,
  filename: electronKey,
  loaded: true,
  exports: fakeElectron,
  children: [],
  paths: [],
};

const { BrowserTabManager } = require(path.join(electronDir, 'browser', 'tabs.js'));
const { BrowserControlServer } = require(path.join(electronDir, 'browser', 'server.js'));

// ---------- 假对象工厂 ----------
// 假 guest webContents：只有真实存在的读取面；刻意不提供 getSize/getBounds（回归即 TypeError）
function makeGuest(id, { url = 'https://local.test/', title = 'Local Test', destroyed = false } = {}) {
  return {
    id,
    isDestroyed: () => destroyed,
    getURL: () => url,
    getTitle: () => title,
    on: () => {},
    close: () => {},
  };
}

function newTabManager(getWindow) {
  const tm = new BrowserTabManager();
  tm.init(getWindow ?? (() => undefined));
  return tm;
}

// 假 CDP 执行器：requireTab 语义与真实实现一致（失效即抛），layoutMetrics 由用例指定
function newServer(tm, metrics) {
  const cdpFake = {
    requireTab: (tabId) => {
      const wc = tm.getWebContents(tabId);
      if (!wc) throw new Error(`浏览器标签 ${tabId} 已失效`);
      return wc;
    },
    layoutMetrics: async () => metrics,
  };
  return new BrowserControlServer(tm, cdpFake);
}

// ---------- 用例 ----------
const steps = [];
function step(name, fn) {
  steps.push([name, fn]);
}

// 视口唯一来源是注入的读取器
step('summaryOf 视口取 viewportResolver 结果', async () => {
  guestRegistry.set(11, makeGuest(11));
  const tm = newTabManager();
  tm.setViewportResolver(async () => ({ width: 1000, height: 700 }));
  tm.tabs.set('a-1', { webContentsId: 11, url: 'https://local.test/', title: 'Local Test', active: true });
  const summary = await tm.summaryOf('a-1');
  assert.deepStrictEqual(summary.viewport, { width: 1000, height: 700 });
});

// 读取器缺失 / 回 null（devtools 占用等不可读场景）→ 回退默认视口，不抛错
step('summaryOf 视口不可读时回退默认', async () => {
  guestRegistry.set(12, makeGuest(12));
  const bare = newTabManager();
  bare.tabs.set('t', { webContentsId: 12, url: '', title: '', active: false });
  assert.deepStrictEqual((await bare.summaryOf('t')).viewport, { width: 1280, height: 720 });

  const withNull = newTabManager();
  withNull.setViewportResolver(async () => null);
  withNull.tabs.set('t', { webContentsId: 12, url: '', title: '', active: false });
  assert.deepStrictEqual((await withNull.summaryOf('t')).viewport, { width: 1280, height: 720 });
});

// getState：视口来自 cdp.layoutMetrics，URL/标题读自 guest 对象
step('dispatch getState 返回 CDP 视口与状态', async () => {
  guestRegistry.set(21, makeGuest(21, { url: 'https://local.test/page', title: 'Page Title' }));
  const tm = newTabManager();
  tm.tabs.set('a-2', { webContentsId: 21, url: 'https://local.test/page', title: 'Page Title', active: true });
  const server = newServer(tm, { width: 1200, height: 900 });
  const result = await server.dispatch({ method: 'getState', tabId: 'a-2' });
  assert.strictEqual(result.ok, true);
  assert.deepStrictEqual(result.state.viewport, { width: 1200, height: 900 });
  assert.strictEqual(result.state.url, 'https://local.test/page');
  assert.strictEqual(result.state.title, 'Page Title');
});

// getState 的 CDP 不可读回退
step('dispatch getState 视口不可读回退默认', async () => {
  guestRegistry.set(22, makeGuest(22));
  const tm = newTabManager();
  tm.tabs.set('a-2b', { webContentsId: 22, url: '', title: '', active: true });
  const server = newServer(tm, null);
  const result = await server.dispatch({ method: 'getState', tabId: 'a-2b' });
  assert.deepStrictEqual(result.state.viewport, { width: 1280, height: 720 });
});

// list：summaries 全量经 async summaryOf，视口字段来自读取器
step('dispatch list 返回带视口的标签摘要', async () => {
  guestRegistry.set(31, makeGuest(31));
  const tm = newTabManager();
  tm.setViewportResolver(async () => ({ width: 1100, height: 850 }));
  tm.tabs.set('a-3', { webContentsId: 31, url: 'https://local.test/', title: 'Local Test', active: true });
  const server = newServer(tm, { width: 1100, height: 850 });
  const result = await server.dispatch({ method: 'list' });
  assert.strictEqual(result.ok, true);
  assert.strictEqual(result.tabs.length, 1);
  assert.strictEqual(result.tabs[0].tabId, 'a-3');
  assert.deepStrictEqual(result.tabs[0].viewport, { width: 1100, height: 850 });
});

// newTab 握手成功路径：渲染侧回报后回包摘要含读取器视口
// （回归曾发生：该路径在 tab 摘要处抛 TypeError，标签建出来却报执行失败）
step('dispatch newTab 握手成功返回视口摘要', async () => {
  const fakeWin = {
    isDestroyed: () => false,
    webContents: {
      send: (channel, payload) => {
        // 模拟渲染侧即时回报 tab-created
        if (channel === 'browser:tab-add') {
          const reply = ipcHandlers.get('browser:tab-created');
          guestRegistry.set(41, makeGuest(41));
          reply(null, { tabId: payload.tabId, webContentsId: 41, url: payload.url, title: '', active: true });
        }
      },
    },
  };
  const tm = newTabManager(() => fakeWin);
  tm.setViewportResolver(async () => ({ width: 1000, height: 700 }));
  const server = newServer(tm, { width: 1000, height: 700 });
  const result = await server.dispatch({ method: 'newTab', url: 'https://local.test/' });
  assert.strictEqual(result.ok, true);
  assert.strictEqual(typeof result.tab.tabId, 'string');
  assert.deepStrictEqual(result.tab.viewport, { width: 1000, height: 700 });
});

// newTab 握手超时：渲染侧不回报 → 拒绝文案为明确的业务错误
// （约 5000ms：TAB_CREATE_TIMEOUT_MS 的真实语义保留，不能被 HTTP 层超时抢先掩盖）
step('newTab 握手超时返回明确业务错误', async () => {
  const fakeWin = {
    isDestroyed: () => false,
    webContents: { send: () => {} }, // 只收不回
  };
  const tm = newTabManager(() => fakeWin);
  const server = newServer(tm, null);
  await assert.rejects(
    () => server.dispatch({ method: 'newTab' }),
    /等待渲染侧创建浏览器标签超时/,
  );
});

// ---------- runner ----------
(async () => {
  let failed = 0;
  for (const [name, fn] of steps) {
    try {
      await fn();
      console.log(`ok - ${name}`);
    } catch (e) {
      failed += 1;
      console.error(`not ok - ${name}: ${e && e.message ? e.message : String(e)}`);
    }
  }
  if (failed) {
    console.error(`FAIL ${failed}/${steps.length}`);
    process.exitCode = 1;
  } else {
    console.log(`PASS all ${steps.length}`);
  }
})();
