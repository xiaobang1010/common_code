# Playwright 定位器纪律

`tab.playwright` 是刻意收敛的 Playwright 兼容面。只调用有效 API 清单中存在的成员。`playwright.evaluate(...)` 与定位器 `evaluate(...)` 在页面上下文执行 JavaScript；需要页面侧计算或交互时使用。

`getByRole(..., { name })` 接受普通字符串或 `RegExp`。优先直接反映最新快照证明的可访问名事实的匹配形态。

## 快照是定位器的事实来源

- 在导航或 UI 变化使其过期之前，保留并复用最近的 `tab.playwright.domSnapshot()`。
- 只从该快照中实际出现的角色、可访问名、文本、placeholder、`data-*`、`href` 或其他属性构建定位器。
- 绝不猜测 label、可访问名、placeholder、选择器、URL 模式或元素类型。猜测的定位器不是探索性探针。
- 轮播式搜索建议不是稳定的 placeholder 约定。快照里出现一个无名 `textbox` 时，优先 `getByRole("textbox")` 加 `count()`，不要发明 `getByPlaceholder("Search")`。
- 不要倾倒 `body` 文本或遍历宽泛定位器来发现页面。用一次有界快照，然后收窄到相关区段或候选。
- 最新快照已包含目标时直接使用其事实。不要调用 `evaluate()` 去重新发现相关元素、枚举输入、倾倒 HTML、遍历 DOM 或探测猜测的选择器。
- 快照证明存在的标题或可见文本不需要 `link` 或 `button` 角色即可点击。不要用猜测的 `link` 角色替换快照证明的 `heading`。
- 用户已授权导航且实际标题/文本目标唯一解析时，直接点击该目标。DOM 点击可以冒泡到祖先卡片上的 JavaScript 处理器，即使目标本身没有可交互 ARIA 角色。

## 执行页面脚本

`playwright.evaluate(...)` 与定位器 `evaluate(...)` 在页面上下文运行给定表达式或函数，可能读取或改变页面状态。高层定位器与动作方法能更清晰表达意图时优先它们；需要直接 JavaScript 访问的页面侧逻辑才用 evaluate。

## 必需交互配方

在 click、fill、press、select、check 或其他改变状态的定位器动作之前：

1. 复用最近相关快照；其定位器事实过期或不完整时拍新快照。
2. 依据这些事实构建最稳定的定位器。
3. 唯一性不显然时调用一次 `count()` 并保留结果。
4. 仅当定位器恰好解析到一个目标元素时继续。
5. 执行动作一次，然后只收集下一个决策所需的定向状态或新快照。每个观察周期最多一个改变状态的动作。

`count() === 0` 时不执行动作、不等待该定位器。拍新快照并重建。计数大于 1 时收窄到稳定容器或更强属性；不要用 `first()`、`last()`、`nth()` 作为歧义捷径。

## 定位器优先级

按持久事实优先，顺序如下：

1. 稳定 test id 或 `data-*` 属性；
2. 稳定精确 `href` 或类似持久属性；
3. 带作用域的语义角色加快照证明的可访问名；
4. 带作用域的可见文本；
5. 从已知 DOM 事实复制的带作用域 CSS 选择器；
6. 带作用域的 `cua` 坐标兜底——当 Playwright 定位器面无法识别一个稳定目标时。

`Search`、`Menu`、`Close` 等通用名或重复的结果标题默认有歧义。行动前先加作用域。

## 超时与恢复

常规定位器、URL/加载状态等待与 evaluate 操作使用短失败预算：默认 3000ms，即使请求更大值也最多 3000ms。显式 `tab.playwright.waitForTimeout(ms)` 是独立的固定延迟，应保持例外。

每次成功的 `tab.goto(url)` 之后，第一次读取标题、URL 或 DOM 前显式调用 `await tab.playwright.waitForLoadState({ state: "domcontentloaded" })`。即使 `goto()` 已让底层导航就绪也保留这一步；它确认预期加载状态，不改变 3000ms 运行上限。

`waitForLoadState({ state: "networkidle" })` 不被当前运行时支持。等 `load`/`domcontentloaded` 或具体页面状态。

`expectNavigation(action)` 在动作前启动加载状态等待器，但已加载的旧页面可能满足该等待器。动作必须证明新导航时传 `{ url: expectedUrl }`。

源标签 URL 未变不能证明点击失败。以预期效果是否出现判断动作，而不是以 `browser.tabs.list()` 非空判断。已存在的源标签或不相关的受控标签不是动作效果。按已验证的源页面状态或标签 URL/标题匹配预期结果。

当操作可能打开弹窗/新标签且源标签未显示预期效果时，在同一观察单元无条件读取受控标签：

```js
const controlledTabs = await browser.tabs.list();
nodeRepl.write({ controlledTabs });
```

把 `{ controlledTabs }` 作为该单元结果返回，让模型从列表做单一决策。不要先返回受控列表再决定是否查询其他来源。下一个单元里激活匹配预期 URL/标题的页面。若源页面与合并标签观察都缺少预期效果，拍新快照并选择有证据的新方案，而不是重放上一次点击。

超时、严格模式失败或选择器解析失败之后：

- 不要重试同一定位器；
- 拍新鲜 `domSnapshot()`；
- 确认目标仍然存在；
- 从更紧作用域或更稳定的快照证明属性重建。

同一目标两次尝试失败后，不要再增加角色/文本复杂度，刻意切换到最强稳定属性或带作用域的 `cua` 坐标路径。
