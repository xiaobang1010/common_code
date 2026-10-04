# 内置浏览器自动化 API

本项目的内置浏览器只有一个后端（桌面端产物区的浏览器标签页）。Playwright 是 `Tab` 的 API 面，不是后端。

每个代码单元都是全新 node 进程：先执行技能 bootstrap、重建选中的浏览器包装器，并完整读取一次其有效文档：

```js
const browser = await agent.browsers.getDefault();
nodeRepl.write(await browser.documentation());
```

开始下一个逻辑标签操作批次时，先返回完整的受控标签观察；模型检视结果后，在下一个代码单元绑定已验证的标签；没有现存页面可用时才新建：

```js
const browser = await agent.browsers.getDefault();
const controlledTabs = await browser.tabs.list();
nodeRepl.write({ controlledTabs });
```

```js
const browser = await agent.browsers.getDefault();
const tab = await browser.tabs.new();
await tab.goto("https://example.com");
await tab.playwright.waitForLoadState({ state: "domcontentloaded" });
nodeRepl.write(await tab.playwright.domSnapshot());
```

每次成功的 `tab.goto(url)` 之后，在第一次读取标题、URL 或 DOM 之前显式调用 `await tab.playwright.waitForLoadState({ state: "domcontentloaded" })`。即使 `goto()` 已让底层导航就绪，也要把这一步保留在模型可见轨迹中。不要用 `networkidle` 或固定 sleep 替代；常规 URL/加载状态等待上限 3000ms。

观察输出必须经 `nodeRepl.write(...)` 打印或显式 `return`——只赋值给局部变量不会把页面状态呈现给模型。

高层方法直接返回负载；动作类成功时返回 `undefined`；命令失败时抛 `BrowserCommandError`。

`playwright.domSnapshot()` 是默认的观察与定位器事实来源。它返回紧凑的 AI/ARIA 树，而不是页面 `outerHTML`。

## API 使用行为

- 每个新代码单元重建同一浏览器包装器。每个新逻辑标签操作批次前，在专门的代码单元调用 `tabs.list()` 并把完整结果打印给模型；检视后用下一个代码单元按意图匹配 id/url/title 并调用 `tabs.get(id)`。跨调用不存在旧的 Browser 或 Tab JavaScript 绑定。同一代码单元内的连续动作可以复用刚验证过的 Tab。
- URL 导航优先用 `await agent.browsers.open(url)`：它复用同站点已有受控标签（同主机名）、激活它让用户看到、并原地导航，而不是每次叠新标签。只有确实需要并行独立标签时才 `{ reuseTab: false }` 或 `browser.tabs.new()`。
- 一切交互基于可见页面状态，而不是 DOM 源码顺序。动作后收集能回答下一个问题的最便宜观察；默认不同时拍快照和截图。
- 快照证明存在的标题或可见文本不需要 `link` 或 `button` 角色即可点击。不要用猜测的 `link` 角色替换快照证明的 `heading`。用户已授权导航且真实目标唯一时直接点击；事件可能冒泡到祖先卡片的 JavaScript 处理器。
- 每个观察周期最多一个改变状态的动作。源标签 URL 未变不能证明点击失败。以预期效果是否出现判断动作，而不是以 `browser.tabs.list()` 非空判断。已存在的源标签或不相关的受控标签不是动作效果。当操作可能打开弹窗/新标签且源标签未显示预期效果时，在同一观察单元里读取 `browser.tabs.list()` 并打印 `{ controlledTabs }`；若其中没有匹配结果，再拍新快照或新建标签。
- 标签已在目标 URL 时不要再 `goto()`。仅确需刷新时用 `reload()`。
- 只读查询时，允许一次源自用户输入或已验证页面事实的定向 URL 尝试。若失败或无法验证，不要循环猜测 URL 变体、查询参数、路径名或数字资源 ID。切换到站点可见的搜索/导航或专用连接器/API/CLI。一旦存在一个权威候选，直接验证而不是继续收集。
- 尽量减少打断。对欠明确但安全的请求，先尝试证据最充分的路线，再考虑提澄清问题。

可用入口：

- `await agent.browsers.list()` 返回宿主注册表的运行时描述符（`id`、`type`、capabilities、metadata）。连接 generation 是内部的过路由守卫。
- `await agent.browsers.getDefault()` 与 `get(idOrType)` 返回 `Browser`；显式选择不可用时直接失败，不静默切换。
- `browser.tabs.list()` 返回全部受控标签的 `TabInfo[]`，含当前 `active` 标记与实际 CSS `viewport: { width, height }`。检视整个列表并按稳定 id 或已验证 URL/标题匹配；绝不用数组位置选多标签目标。
- `browser.tabs.get(tabId)` 校验、绑定并激活标签。
- `browser.tabs.new()` 创建真实内置浏览器标签，并在其 guest 就绪确认后返回。
- 浏览器标签在应用会话内跨轮次持续存在，直到显式 `tab.close()`、用户关闭标签、或用户关闭「浏览器」工具标签/折叠产物区触发回收。回收后 `tabs.list()` 返回空集，重新 `tabs.new()` 会自动展开面板重建。
- 创建内置浏览器标签会自动打开右侧面板并激活该标签，让用户看见浏览器使用过程。
- 仅当任务明确需要再次隐藏或显示面板时用 `await (await browser.capabilities.get("visibility")).set(false | true)`。
- `agent.documentation.get("screenshots")` 仅在真正需要视觉证据时加载截图指引。

核心 `Tab` 方法：

- `id`、`url()`、`title()`
- `goto(url)`
- `back()`、`forward()`、`reload()`、`close()`
- `screenshot(opts?)`
- `setViewportSize({ width, height })`、`viewportSize()` — Playwright 兼容的响应式视口控制。宽度 320–3840、高度 320–2160；非法输入直接失败不做钳制。
- `capabilities`、`cua`、`playwright`

逃生通道：

- `tab.cua` 是 canvas 与自绘控件的坐标路径。
- `cua.drag({ path })` 保留每个给定点。`cua.scroll({ x, y, scrollX, scrollY })` 从给定视口锚点滚动。
- CUA `keypress({ keys })` 把键序列当作一个组合，不是独立按键序列。
- `tab.playwright` 暴露常用 Playwright 面：`locator/getBy*/frameLocator`、定位器动作与查询、`evaluate`、`domSnapshot`、`waitForURL`、`waitForLoadState`、`waitForTimeout`、`expectNavigation`。
- 固定等待是 `tab.playwright.waitForTimeout(timeoutMs)`，不存在 `tab.waitForTimeout`。优先 `locator.waitFor(...)`、`waitForURL(...)`、`waitForLoadState(...)` 或一次新鲜的语义观察。
- 常规定位器、URL/加载状态等待与 evaluate 操作默认且上限 3000ms。超时是刷新快照、重建定位器的信号，不是原样重试的信号。
- 内置浏览器不支持文件上传：`waitForEvent("filechooser")` / `fileChooser.setFiles(...)` 会以 `capability_unsupported` 失败，不暴露伪造的上传成功。
