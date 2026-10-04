# Workflow

以下每个代码块都假设 `control-browser` 技能的 bootstrap 已在当前代码单元执行。每个调用重建同一浏览器包装器；提供连续性的是浏览器标签，不是 JavaScript 变量。

1. 每个逻辑标签操作批次以一个专门的代码单元开头，把全部受控标签打印给模型：

```js
const browser = await agent.browsers.getDefault();
const controlledTabs = await browser.tabs.list();
nodeRepl.write({ controlledTabs });
```

检视该输出后，用下一个代码单元按稳定 id 或已验证 URL/标题匹配目标页面，再调用 `tabs.get(id)` 激活。绝不因为列表非空就选 `[0]`。没有受控标签匹配时，新建标签前先确认 `tabs.list()` 确实不含目标。这是操作前的目标选择协议；操作结果的弹窗观察用第 5 步的合并单元。

```js
const browser = await agent.browsers.getDefault();
const tab = await browser.tabs.get("上一列表里验证过的标签id");
nodeRepl.write(await tab.playwright.domSnapshot());
```

2. 任务点名新 URL 时，优先用复用入口打开或导航一次：

```js
const tab = await agent.browsers.open("https://example.com");
await tab.playwright.waitForLoadState({ state: "domcontentloaded" });
nodeRepl.write(await tab.playwright.domSnapshot());
```

确实需要并行独立标签时才新建：

```js
const browser = await agent.browsers.getDefault();
const tab = await browser.tabs.new();
await tab.goto("https://example.com");
await tab.playwright.waitForLoadState({ state: "domcontentloaded" });
nodeRepl.write(await tab.playwright.domSnapshot());
```

每次成功的 `tab.goto(url)` 之后，第一次读取标题、URL 或 DOM 前必须显式调用 `await tab.playwright.waitForLoadState({ state: "domcontentloaded" })`。即使底层导航已就绪也要保留这一显式确认。不要用 `networkidle` 或固定 sleep 替代；常规 URL/加载状态等待上限 3000ms。

3. 从 `playwright.domSnapshot()` 读取页面。它返回含计算角色、可访问名、状态与可得 iframe 内容的紧凑 AI/ARIA 树。只从最近相关快照中存在的事实构建 Playwright 定位器。快照已包含目标时直接使用，不要写 `evaluate()` 去搜索相关元素、枚举输入、倾倒 HTML 或遍历 DOM。绝不猜测 label、可访问名、placeholder、选择器或 URL 模式，也不要把猜测的定位器当探索性探针消耗超时预算。

快照证明存在的标题或可见文本不需要 `link` 或 `button` 角色即可点击。不要用猜测的 `link` 角色替换快照证明的 `heading`。用户已授权导航且实际标题/文本定位器唯一时直接点击；其事件可以在祖先卡片上冒泡到 JavaScript 处理器。

快照调用必须经 `nodeRepl.write(...)` 打印或显式 `return`——只赋值给局部变量不会把 DOM 观察返回给模型。

4. 唯一性不明显时先确认定位器唯一，再经真实浏览器动作交互。`count()` 为零时不要等待或执行该定位器：拍新快照并重建。大于 1 时收紧范围，而不是取位置捷径：

```js
const input = tab.playwright.getByRole("textbox", { name: "Search" });
if ((await input.count()) !== 1) throw new Error("Search locator is not unique");
await input.fill("hello");
await input.press("Enter");
```

5. 动作之后，收集能回答下一个问题的最便宜观察。优先定向定位器状态检查；需要新的定位器事实时再拍一次 `domSnapshot()`。每个观察周期最多一个改变状态的动作。源标签 URL 未变不能证明点击失败。以预期效果是否出现判断动作，而不是以 `browser.tabs.list()` 非空判断。已存在的源标签或不相关的受控标签不是动作效果。预期效果可以是源页面状态变化，或经验证 URL/标题匹配预期结果的标签。

当操作可能打开弹窗/新标签且源标签未显示预期效果时，在同一观察单元无条件读取受控标签：

```js
const controlledTabs = await browser.tabs.list();
nodeRepl.write({ controlledTabs });
```

把 `{ controlledTabs }` 作为该单元的结果返回，让模型从列表做单一决策。下一个单元里按已验证 id/url/title 匹配并激活目标页面。若源页面与合并标签观察都缺少预期效果，拍新快照并选择新定位器，而不是重放旧点击。打开或导航普通页面不是截图的理由；默认不同时收集 DOM 快照和截图。

仅当用户明确请求截图、需要判断视觉布局/渲染/图像内容、或 DOM 快照缺少必需目标（例如 canvas/自绘 UI）时，才加载 `agent.documentation.get("screenshots")`。一旦选定该分支，每张截图都必须在同一代码单元经 `nodeRepl.emitImage(await tab.screenshot())` 发出；绝不把 `tab.screenshot()` 留作最终表达式或直接返回其 `Uint8Array` 字节。

任何 Playwright 超时、严格模式失败或选择器解析失败之后，不要重试同一定位器。拍新鲜 `domSnapshot()` 并从快照证明的事实重建。常规定位器与页面状态等待在 3000ms 预算内失败；只有确实无任何具体状态可观察时才用更长的固定等待。

对无法经高层定位器 API 表达的页面侧 JavaScript，用 `playwright.evaluate(...)` 与定位器 `evaluate(...)`。这些调用在页面上下文执行，保持表达式聚焦；正常动作方法能更清晰表达意图时就用正常方法。

6. 标签默认跨轮次保持打开。关闭只经有意的 `await tab.close()` 调用。用户关闭「浏览器」工具标签或折叠产物区会回收全部标签——回收后 `tabs.list()` 返回空集，重新 `tabs.new()` 自动展开面板重建。

对直接查询 URL，最多做一次源自用户输入或已验证页面事实的定向尝试。绝不迭代猜测的 URL 变体、路径、搜索参数或数字 ID。定向尝试失败后，用新鲜快照、站点自身搜索/导航或权威连接器/API/CLI 查询，然后再导航。
