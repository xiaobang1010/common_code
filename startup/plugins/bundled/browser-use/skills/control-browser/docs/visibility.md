# 浏览器面板可见性指引

- 创建内置浏览器标签会自动打开并激活右侧浏览器面板，让用户看见浏览器使用过程。
- 正常浏览器工作期间保持面板可见，除非任务明确要求隐藏。
- 用可见性控制隐藏面板或再次显示；`tabs.new()` 之后调用方不需要再调 `set(true)`。
- 经 `await (await browser.capabilities.get("visibility")).set(true | false)` 显示或隐藏；用 `get()` 读取当前状态。
