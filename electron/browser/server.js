// 内置浏览器 HTTP 控制服务：仅监听 127.0.0.1，随机端口 + Bearer 令牌鉴权。
// 技能侧的 runner 经桥接文件发现端口与令牌，把控制命令 POST 进来，由主进程转译执行。
// 响应包裹：{ok:true, <命令对应顶层字段>} / {ok:false, error:{code,message}}，
// 负载键随命令而定（tabs/tab/state/image/value/element）；绝不统一包成 {ok,value}，
// 否则每个命令都会解包失败。
const http = require('http')
const crypto = require('crypto')
const fs = require('fs')
const path = require('path')
const { BrowserError, isBrowserError } = require('./cdp')
const { PAGE_RUNTIME_SOURCE } = require('./page-runtime')

// 请求级超时：常规命令上限；locator/evaluate 类沿用 3s 预算语义，握手留余量
const REQUEST_TIMEOUT_MS = 5000
const MAX_BODY_BYTES = 2 * 1024 * 1024

// 描述符：type 固定 iab 是能力门控（按 unsupportedByDefaultIn 隐藏成员与文档）生效的前提，
// 不得改成中性命名；录制类 API 不在 iab 隐藏列表内，靠文档资产裁剪不外泄。
const BROWSER_DESCRIPTOR = {
  id: 'inapp',
  generation: 1,
  type: 'iab',
  name: '内置浏览器',
  capabilities: {
    browser: [
      { id: 'visibility', description: '显示或隐藏内置浏览器面板' },
    ],
    tab: [
      { id: 'playwright', description: 'Playwright 定位器与 DOM 快照' },
      { id: 'screenshot', description: '页面截图' },
      { id: 'viewport', description: '视口尺寸设置' },
    ],
  },
}

class BrowserControlServer {
  constructor(tabManager, cdp) {
    this.tabManager = tabManager
    this.cdp = cdp
    this.token = crypto.randomBytes(24).toString('hex')
    this.server = null
    this.port = 0
    // 已注入运行时库的 guest webContentsId 集合（导航后需重注入）
    this.runtimeInjected = new Set()
    // 已挂 did-navigate 清理钩子的 guest 集合（防重复监听）
    this.navHooked = new Set()
  }

  // 启动并写桥接文件（临时文件 + 原子 rename，避免读到半截内容）
  async start() {
    await new Promise((resolve, reject) => {
      this.server = http.createServer((req, res) => this.handle(req, res))
      this.server.listen(0, '127.0.0.1', resolve)
      this.server.on('error', reject)
    })
    this.port = this.server.address().port
    const bridgePath = process.env.COMMON_CODE_BROWSER_BRIDGE_FILE
    if (bridgePath) {
      try {
        fs.mkdirSync(path.dirname(bridgePath), { recursive: true })
        const tmp = bridgePath + '.tmp'
        fs.writeFileSync(tmp, JSON.stringify({ port: this.port, token: this.token }))
        fs.renameSync(tmp, bridgePath)
      } catch (e) {
        console.error('浏览器桥接文件写入失败:', e.message)
      }
    }
  }

  stop() {
    if (this.server) this.server.close()
    const bridgePath = process.env.COMMON_CODE_BROWSER_BRIDGE_FILE
    if (bridgePath) {
      try { fs.unlinkSync(bridgePath) } catch { /* 不存在则忽略 */ }
    }
  }

  handle(req, res) {
    // 仅放行本机来源
    const remote = req.socket.remoteAddress || ''
    if (remote !== '127.0.0.1' && remote !== '::1' && remote !== '::ffff:127.0.0.1') {
      return this.json(res, 401, { ok: false, error: { code: 'unauthorized', message: '仅允许本机访问' } })
    }
    const auth = req.headers.authorization || ''
    if (auth !== 'Bearer ' + this.token) {
      return this.json(res, 401, { ok: false, error: { code: 'unauthorized', message: '令牌无效' } })
    }

    if (req.method === 'GET' && req.url === '/browsers') {
      return this.json(res, 200, { ok: true, browsers: [BROWSER_DESCRIPTOR] })
    }

    if (req.method === 'POST' && req.url === '/execute') {
      let body = ''
      let overflow = false
      req.on('data', (chunk) => {
        if (overflow) return
        body += chunk
        if (body.length > MAX_BODY_BYTES) {
          overflow = true
          this.json(res, 413, { ok: false, error: { code: 'payload_too_large', message: '请求体过大' } })
          req.destroy()
        }
      })
      req.on('end', async () => {
        if (overflow) return
        let payload
        try {
          payload = JSON.parse(body)
        } catch {
          return this.json(res, 400, { ok: false, error: { code: 'bad_request', message: '请求体不是合法 JSON' } })
        }
        const command = payload.command || {}
        try {
          const result = await this.withTimeout(this.dispatch(command))
          this.json(res, 200, result)
        } catch (e) {
          const code = isBrowserError(e) ? e.code : 'execution_error'
          this.json(res, 200, { ok: false, error: { code, message: e.message } })
        }
      })
      return
    }

    this.json(res, 404, { ok: false, error: { code: 'not_found', message: '未知端点' } })
  }

  json(res, status, obj) {
    if (res.writableEnded) return
    res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8' })
    res.end(JSON.stringify(obj))
  }

  withTimeout(promise) {
    let timer
    return Promise.race([
      promise.finally(() => clearTimeout(timer)),
      new Promise((_, reject) => {
        timer = setTimeout(() => reject(new BrowserError('timeout', '命令执行超时')), REQUEST_TIMEOUT_MS)
      }),
    ])
  }

  // ---------- 命令路由（三态表：已实现 / 结构化不支持） ----------
  async dispatch(command) {
    const method = command.method
    const tabId = command.tabId

    switch (method) {
      case 'list':
        return { ok: true, tabs: this.tabManager.summaries() }

      case 'newTab': {
        const created = await this.tabManager.createTab(command.url)
        return { ok: true, tab: this.summaryOf(created) }
      }

      case 'activateTab': {
        this.tabManager.activateTab(tabId)
        return { ok: true, tab: this.summaryOf(tabId) }
      }

      case 'close':
        this.tabManager.removeTab(tabId)
        return { ok: true }

      case 'navigate': {
        const wc = this.cdp.requireTab(tabId)
        if (!/^https?:\/\//i.test(command.url || '') && command.url !== 'about:blank') {
          throw new BrowserError('bad_request', '仅允许 http/https 与 about:blank 导航')
        }
        await wc.loadURL(command.url)
        return { ok: true }
      }

      case 'back':
        this.cdp.requireTab(tabId).goBack()
        return { ok: true }

      case 'forward':
        this.cdp.requireTab(tabId).goForward()
        return { ok: true }

      case 'reload':
        this.cdp.requireTab(tabId).reload()
        return { ok: true }

      case 'getState': {
        const wc = this.cdp.requireTab(tabId)
        const [width, height] = wc.getSize()
        return {
          ok: true,
          state: { url: wc.getURL(), title: wc.getTitle(), viewport: { width, height } },
        }
      }

      case 'screenshot': {
        const image = await this.cdp.screenshot(tabId)
        return { ok: true, image }
      }

      case 'evaluate': {
        const value = await this.cdp.evaluate(tabId, command.expression)
        return { ok: true, value }
      }

      case 'elementInfo': {
        // 裸 wire 命令与 playwright 动作共用页内实现，负载键按协议为 element
        const r = await this.runInPage(tabId, { name: 'elementInfo', x: command.x, y: command.y })
        return { ok: true, element: r ? r.value : undefined }
      }

      case 'playwright':
        return this.dispatchPlaywright(tabId, command.action || {})

      case 'playwrightWaitForTimeout':
        await new Promise((r) => setTimeout(r, Math.max(0, Math.min(command.timeoutMs || 0, 30000))))
        return { ok: true }

      case 'browserViewportSet': {
        await this.cdp.setViewport(tabId, command.width, command.height)
        return { ok: true }
      }

      case 'browserVisibilityGet':
        return { ok: true, value: this.tabManager.visible }

      case 'browserVisibilitySet': {
        const win = this.tabManager.getWindow()
        if (win && !win.isDestroyed()) {
          win.webContents.send('browser:pane-visibility', { visible: !!command.visible })
        }
        return { ok: true }
      }

      // ---- 坐标输入（cua 面）----
      case 'click': {
        if (command.ref !== undefined) throw new BrowserError('unsupported', 'dom 节点引用输入不受支持，请使用坐标输入')
        await this.cdp.mouseClick(tabId, command.x, command.y, !!command.doubleClick)
        return { ok: true }
      }

      case 'hover':
        await this.cdp.mouseHover(tabId, command.x, command.y)
        return { ok: true }

      case 'scroll':
        await this.cdp.mouseWheel(tabId, command.x || 0, command.y || 0, command.scrollX || 0, command.scrollY || 0)
        return { ok: true }

      case 'cuaScroll':
        await this.cdp.mouseWheel(tabId, command.x || 0, command.y || 0, command.scrollX || 0, command.scrollY || 0)
        return { ok: true }

      case 'drag': {
        if (command.fromRef !== undefined || command.toRef !== undefined) {
          throw new BrowserError('unsupported', 'dom 节点引用拖拽不受支持，请使用坐标拖拽')
        }
        const from = command.from || {}
        const to = command.to || {}
        await this.cdp.mouseDrag(tabId, from.x, from.y, to.x, to.y)
        return { ok: true }
      }

      case 'cuaDrag': {
        const from = command.from || {}
        const to = command.to || {}
        await this.cdp.mouseDrag(tabId, from.x, from.y, to.x, to.y)
        return { ok: true }
      }

      case 'type':
        if (command.ref !== undefined) throw new BrowserError('unsupported', 'dom 节点引用输入不受支持')
        await this.cdp.typeText(tabId, command.text)
        return { ok: true }

      case 'press':
        if (command.ref !== undefined) throw new BrowserError('unsupported', 'dom 节点引用输入不受支持')
        await this.cdp.keyCombo(tabId, command.key, command.modifiers)
        return { ok: true }

      case 'cuaKeypress': {
        // keys 为数组时逐键处理
        const keys = Array.isArray(command.keys) ? command.keys : [command.keys]
        for (const k of keys) await this.cdp.keyCombo(tabId, k)
        return { ok: true }
      }

      case 'check':
      case 'select':
        // 这两个 tab 级命令仅 dom_cua 面（ref 寻址）使用，本项目未实现该面
        throw new BrowserError('unsupported', 'dom 节点引用操作不受支持，请经 playwright 定位器完成')

      // ---- 结构化不支持（SKILL.md 已删除对应分支，正常流程不会触达）----
      case 'recordingStart':
      case 'recordingStatus':
      case 'recordingCancel':
      case 'getDialog':
      case 'handleDialog':
      case 'claimTab':
      case 'listUserTabs':
      case 'finalize':
      case 'finalizeTabs':
      case 'markDeliverable':
      case 'markHandoff':
      case 'nameSession':
      case 'snapshot':
      case 'domCuaScroll':
        throw new BrowserError('unsupported', `命令 ${method} 在本项目内置浏览器中不受支持`)

      default:
        throw new BrowserError('unknown_method', `未知命令：${method}`)
    }
  }

  // 注册表条目 → 协议摘要（含 viewport），供 newTab/activateTab 回包
  summaryOf(tabId) {
    return this.tabManager.summaryOf(tabId)
  }

  // ---------- playwright 命令分发 ----------
  async dispatchPlaywright(tabId, action) {
    // 取值类动作统一走注入库；点击/悬停/按键类先拿页内坐标再补真实输入
    const dispatchResult = await this.runInPage(tabId, action)

    if (dispatchResult && dispatchResult.input === 'click') {
      await this.cdp.mouseClick(tabId, dispatchResult.x, dispatchResult.y, dispatchResult.clickCount > 1)
      return { ok: true }
    }
    if (dispatchResult && dispatchResult.input === 'hover') {
      await this.cdp.mouseHover(tabId, dispatchResult.x, dispatchResult.y)
      return { ok: true }
    }
    if (dispatchResult && dispatchResult.input === 'press') {
      if (dispatchResult.modifiers && dispatchResult.modifiers.length) {
        await this.cdp.keyCombo(tabId, dispatchResult.key, dispatchResult.modifiers)
      } else {
        await this.cdp.keyPress(tabId, dispatchResult.key)
      }
      return { ok: true }
    }

    // elementScreenshot 复用截图实现（playwright 动作，非独立 wire method）
    if (action.name === 'elementScreenshot') {
      const image = await this.cdp.screenshot(tabId)
      return { ok: true, image }
    }

    return { ok: true, value: dispatchResult ? dispatchResult.value : undefined }
  }

  // 确保页面注入运行时库后调用 dispatch；guest 导航后页面上下文重置，
  // did-navigate 事件清掉注入标记，下次命令自动重注入
  async runInPage(tabId, action) {
    const wc = this.cdp.requireTab(tabId)
    if (!this.runtimeInjected.has(wc.id)) {
      await this.cdp.evaluate(tabId, PAGE_RUNTIME_SOURCE)
      if (!this.navHooked.has(wc.id)) {
        this.navHooked.add(wc.id)
        wc.on('did-navigate', () => this.runtimeInjected.delete(wc.id))
      }
      this.runtimeInjected.add(wc.id)
    }
    // dispatch 是 async 函数，cdp.evaluate 的 awaitPromise 会等它完成再回传值
    const expression = `window.__inappBrowser.dispatch(${JSON.stringify(action)})`
    return this.cdp.evaluate(tabId, expression)
  }
}

module.exports = { BrowserControlServer }
