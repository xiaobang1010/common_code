# 截图

这是仅按需查询的指引。DOM 快照能回答问题时，不要把它用于普通导航、读取、搜索或表单交互。

仅当用户明确请求截图、需要判断视觉布局/渲染/图像内容、或必需目标不在 DOM 快照里时，才捕获截图。默认不要同时请求快照和截图。

`await tab.screenshot(opts?)` 内部返回 PNG 字节。这些字节不是模型可见的截图，绝不能作为代码单元的返回结果直接给出。

每次截图都必须在同一代码单元把字节交给 `nodeRepl.emitImage`，执行器会把图片落盘为工作区 `.browser-shots/` 下的文件并打印路径，随后用文件读取工具查看：

```js
nodeRepl.emitImage(await tab.screenshot());
```

绝不要把 `await tab.screenshot()` 留作最终表达式。

支持的截图选项：

- `{ fullPage: true }` 捕获整页。
- `{ clip: { x, y, width, height } }` 捕获视口区域。

截图超时时，不要立即重发同一截图。底层捕获可能仍在完成；先等待，或在显式的进行中错误未清除时重开标签。
