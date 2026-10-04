// CDP（Chrome DevTools Protocol）执行器：截图、页面求值、坐标输入。
// 每次命令临时 attach、用完 detach——与用户打开的开发者工具互斥（devtools 调起会
// 触发 debugger detach），不长期占用通道，用户关闭 devtools 后自动恢复。
const BROWSER_ERROR = Symbol('browserError')

// 带协议错误码的异常，控制服务据此回 {ok:false, error:{code,message}}
class BrowserError extends Error {
  constructor(code, message) {
    super(message)
    this[BROWSER_ERROR] = true
    this.code = code
  }
}

function isBrowserError(e) {
  return !!(e && e[BROWSER_ERROR])
}

// 把 CDP 返回的异常细节映射为可读错误
function cdpFail(command, message) {
  throw new BrowserError('execution_error', `${command} 执行失败：${message}`)
}

class CdpExecutor {
  constructor(tabManager) {
    this.tabManager = tabManager
  }

  // 解析 tabId 对应的 guest webContents，失效即抛
  requireTab(tabId) {
    const wc = this.tabManager.getWebContents(tabId)
    if (!wc) throw new BrowserError('backend_unavailable', `浏览器标签 ${tabId} 已失效`)
    return wc
  }

  // 在 guest 上执行一段 CDP 会话：attach → 回调 → detach
  async withDebugger(tabId, fn) {
    const wc = this.requireTab(tabId)
    const dbg = wc.debugger
    if (!dbg.isAttached()) {
      try {
        dbg.attach('1.3')
      } catch {
        throw new BrowserError(
          'devtools_occupied',
          '无法附加调试器：请关闭该标签页的开发者工具后重试',
        )
      }
    }
    try {
      return await fn(dbg)
    } finally {
      try { dbg.detach() } catch { /* 页面已销毁等场景忽略 */ }
    }
  }

  // 截图：返回 base64 PNG（协议约定负载字段为 image.base64）
  async screenshot(tabId) {
    const { data } = await this.withDebugger(tabId, (dbg) =>
      dbg.sendCommand('Page.captureScreenshot', { format: 'png' }),
    )
    return { base64: data }
  }

  // 页面求值：表达式求值并回传值（awaitPromise 支持顶层 await 形态）
  async evaluate(tabId, expression, arg) {
    const expressionText = arg === undefined
      ? expression
      : `(${expression})(${JSON.stringify(arg)})`
    return this.withDebugger(tabId, async (dbg) => {
      const result = await dbg.sendCommand('Runtime.evaluate', {
        expression: expressionText,
        returnByValue: true,
        awaitPromise: true,
      })
      if (result.exceptionDetails) {
        const detail = result.exceptionDetails.exception
          ? (result.exceptionDetails.exception.description || result.exceptionDetails.exception.value)
          : result.exceptionDetails.text
        cdpFail('evaluate', String(detail))
      }
      return result.result ? result.result.value : undefined
    })
  }

  // 坐标级真实输入：鼠标按下/抬起（双击用 clickCount=2）
  async mouseClick(tabId, x, y, doubleClick) {
    await this.withDebugger(tabId, async (dbg) => {
      const base = { x, y, button: 'left' }
      const clickCount = doubleClick ? 2 : 1
      await dbg.sendCommand('Input.dispatchMouseEvent', { ...base, type: 'mousePressed', clickCount })
      await dbg.sendCommand('Input.dispatchMouseEvent', { ...base, type: 'mouseReleased', clickCount })
    })
  }

  async mouseHover(tabId, x, y) {
    await this.withDebugger(tabId, (dbg) =>
      dbg.sendCommand('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y }),
    )
  }

  // 滚轮：以 (x,y) 为锚点滚动 scrollX/scrollY（负值向上）
  async mouseWheel(tabId, x, y, scrollX, scrollY) {
    await this.withDebugger(tabId, (dbg) =>
      dbg.sendCommand('Input.dispatchMouseEvent', {
        type: 'mouseWheel', x, y, deltaX: scrollX, deltaY: scrollY,
      }),
    )
  }

  // 拖拽：按下起点 → 分步移动 → 抬起终点（中间插几步保证页面收到 drag 事件）
  async mouseDrag(tabId, fromX, fromY, toX, toY) {
    await this.withDebugger(tabId, async (dbg) => {
      await dbg.sendCommand('Input.dispatchMouseEvent', { type: 'mouseMoved', x: fromX, y: fromY })
      await dbg.sendCommand('Input.dispatchMouseEvent', { type: 'mousePressed', x: fromX, y: fromY, button: 'left', clickCount: 1 })
      const steps = 8
      for (let i = 1; i <= steps; i++) {
        await dbg.sendCommand('Input.dispatchMouseEvent', {
          type: 'mouseMoved',
          x: fromX + ((toX - fromX) * i) / steps,
          y: fromY + ((toY - fromY) * i) / steps,
          button: 'left',
        })
      }
      await dbg.sendCommand('Input.dispatchMouseEvent', { type: 'mouseReleased', x: toX, y: toY, button: 'left', clickCount: 1 })
    })
  }

  // 键盘：rawKeyDown + char + keyUp 组合，兼容输入框与快捷键两种场景
  async keyPress(tabId, key) {
    await this.withDebugger(tabId, async (dbg) => {
      const isChar = key.length === 1
      await dbg.sendCommand('Input.dispatchKeyEvent', { type: 'rawKeyDown', key })
      if (isChar) {
        await dbg.sendCommand('Input.dispatchKeyEvent', { type: 'char', text: key })
      }
      await dbg.sendCommand('Input.dispatchKeyEvent', { type: 'keyUp', key })
    })
  }

  // 逐字符输入文本（cua.type 语义：模拟真实键盘，触发页面按键监听）
  async typeText(tabId, text) {
    for (const ch of String(text)) {
      await this.keyPress(tabId, ch)
    }
  }

  // 组合键：按 modifiers 修饰按下主键再抬起
  async keyCombo(tabId, key, modifiers) {
    const maskFor = { Alt: 1, Control: 2, Meta: 4, Shift: 8 }
    let modifierMask = 0
    for (const m of modifiers || []) modifierMask |= maskFor[m] || 0
    await this.withDebugger(tabId, async (dbg) => {
      await dbg.sendCommand('Input.dispatchKeyEvent', { type: 'rawKeyDown', key, modifiers: modifierMask })
      await dbg.sendCommand('Input.dispatchKeyEvent', { type: 'keyUp', key, modifiers: modifierMask })
    })
  }

  // 视口尺寸：用设备度量覆盖模拟指定视口（不影响窗口实际大小）
  async setViewport(tabId, width, height) {
    await this.withDebugger(tabId, (dbg) =>
      dbg.sendCommand('Emulation.setDeviceMetricsOverride', {
        width, height, deviceScaleFactor: 0, mobile: false,
      }),
    )
  }
}

module.exports = { CdpExecutor, BrowserError, isBrowserError }
