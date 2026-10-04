# 浏览器能力：视口

仅在响应式或设备尺寸测试时用显式视口。否则保持内置浏览器的正常视口。

```js
await tab.setViewportSize({ width: 1280, height: 720 });
nodeRepl.write(JSON.stringify(tab.viewportSize()));
```

`setViewportSize()` 以设备度量覆盖模拟指定视口，不影响窗口实际大小。宽高为 CSS 像素，取值范围 320–3840 / 320–2160；非法输入直接失败。
