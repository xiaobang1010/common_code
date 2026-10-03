// 内置浏览器标签注册表：tabId ↔ guest webContentsId 的映射与生命周期管理。
// 渲染侧（BrowserPane）负责挂载 <webview> 并把 guest 的 webContentsId 与状态回报过来；
// 智能体经控制服务发起的建标签动作由主进程生成 tabId 转发渲染侧执行，等待回报完成。
const { app, ipcMain, shell, webContents } = require('electron')

// 固定持久化分区：cookie/登录态跨会话保留，与用户手动浏览和智能体操作共享同一会话
const BROWSER_PARTITION = 'persist:inapp-browser'
// 等待渲染侧建标签回报的时限（毫秒）
const TAB_CREATE_TIMEOUT_MS = 5000

class BrowserTabManager {
  constructor() {
    // tabId -> { webContentsId, url, title, active }
    this.tabs = new Map()
    // 等待渲染侧 tab-created 回报的挂起请求：tabId -> { resolve, reject, timer }
    this.pending = new Map()
    // 主进程侧（agent 发起）的 tabId 计数；渲染侧手动新建用独立前缀不冲突
    this.agentCounter = 0
    this.getWindow = null
    // 浏览器面板当前是否对用户可见（渲染侧回报）
    this.visible = false
  }

  init(getWindow) {
    this.getWindow = getWindow
    this.registerIpc()
    this.registerGuestGuards()
  }

  // guest webContents 的弹窗/外链策略与主窗口现行白名单一致：
  // 仅 webview 类型挂载（该事件对主窗口/devtools 等所有 webContents 触发，不得覆盖主窗口 handler）
  registerGuestGuards() {
    app.on('web-contents-created', (_event, contents) => {
      if (contents.getType() !== 'webview') return
      contents.setWindowOpenHandler(({ url }) => {
        if (/^https?:\/\//i.test(url)) {
          shell.openExternal(url)
        }
        return { action: 'deny' }
      })
    })
  }

  registerIpc() {
    // 渲染侧建好 webview 后回报：注册进表；若命中 agent 发起的挂起请求则完成握手
    ipcMain.on('browser:tab-created', (_event, info) => {
      if (!info || typeof info.tabId !== 'string' || typeof info.webContentsId !== 'number') return
      this.tabs.set(info.tabId, {
        webContentsId: info.webContentsId,
        url: info.url || '',
        title: info.title || '',
        active: info.active === true,
      })
      const p = this.pending.get(info.tabId)
      if (p) {
        clearTimeout(p.timer)
        this.pending.delete(info.tabId)
        p.resolve(this.tabs.get(info.tabId))
      }
    })

    // 导航/标题变化回报
    ipcMain.on('browser:tab-state', (_event, info) => {
      if (!info || !this.tabs.has(info.tabId)) return
      const tab = this.tabs.get(info.tabId)
      if (typeof info.url === 'string') tab.url = info.url
      if (typeof info.title === 'string') tab.title = info.title
    })

    // 激活态变化回报（渲染侧标签条点击切换）
    ipcMain.on('browser:tab-active', (_event, info) => {
      if (!info || typeof info.tabId !== 'string') return
      for (const [id, tab] of this.tabs) tab.active = id === info.tabId
    })

    // 渲染侧关闭标签
    ipcMain.on('browser:tab-closed', (_event, info) => {
      if (!info || !info.tabId) return
      this.dropTab(info.tabId)
    })

    // 浏览器工具标签被移出/面板卸载：销毁全部 guest 并清空注册表
    ipcMain.on('browser:teardown', () => this.teardown())

    // 面板可见性回报（browser 工具标签是否处于激活显示状态）
    ipcMain.on('browser:visibility', (_event, info) => {
      this.visible = !!(info && info.visible)
    })
  }

  // 渲染侧建标签的超时兜底：超时即拒绝并清理挂起项
  requestTabAdd(tabId, url) {
    const win = this.getWindow && this.getWindow()
    if (!win || win.isDestroyed()) {
      return Promise.reject(new Error('主窗口不可用，无法创建浏览器标签'))
    }
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(tabId)
        reject(new Error('等待渲染侧创建浏览器标签超时'))
      }, TAB_CREATE_TIMEOUT_MS)
      this.pending.set(tabId, { resolve, reject, timer })
      win.webContents.send('browser:tab-add', { tabId, url: url || '' })
    })
  }

  // agent 发起新建标签：主进程生成 tabId，转发渲染侧挂载并等待回报
  async createTab(url) {
    const tabId = `a-${++this.agentCounter}`
    await this.requestTabAdd(tabId, url)
    // 建好后立即激活，保证用户可见
    this.activateTab(tabId)
    return tabId
  }

  activateTab(tabId) {
    const tab = this.tabs.get(tabId)
    if (!tab) throw new Error(`浏览器标签 ${tabId} 不存在`)
    for (const [id, t] of this.tabs) t.active = id === tabId
    const win = this.getWindow && this.getWindow()
    if (win && !win.isDestroyed()) win.webContents.send('browser:tab-activate', { tabId })
    return tab
  }

  // agent 发起关闭：通知渲染侧卸载 webview（guest 随元素销毁），主进程同步清账
  removeTab(tabId) {
    if (!this.tabs.has(tabId)) throw new Error(`浏览器标签 ${tabId} 不存在`)
    const win = this.getWindow && this.getWindow()
    if (win && !win.isDestroyed()) win.webContents.send('browser:tab-remove', { tabId })
    this.dropTab(tabId)
  }

  dropTab(tabId) {
    this.tabs.delete(tabId)
    const p = this.pending.get(tabId)
    if (p) {
      clearTimeout(p.timer)
      this.pending.delete(tabId)
      p.reject(new Error('浏览器标签已关闭'))
    }
  }

  // 回收：渲染侧卸载/面板折叠时调用。guest 随 webview 卸载销毁，
  // 但若渲染侧已先行销毁则主进程兜底 close，避免孤儿进程
  teardown() {
    for (const [tabId, tab] of this.tabs) {
      const wc = webContents.fromId(tab.webContentsId)
      if (wc && !wc.isDestroyed()) {
        try { wc.close() } catch { /* 已销毁时忽略 */ }
      }
      this.dropTab(tabId)
    }
    this.tabs.clear()
    this.visible = false
  }

  get(tabId) {
    return this.tabs.get(tabId) || null
  }

  // 取 guest webContents（供 CDP 执行器使用），失效时返回 null
  getWebContents(tabId) {
    const tab = this.tabs.get(tabId)
    if (!tab) return null
    const wc = webContents.fromId(tab.webContentsId)
    return wc && !wc.isDestroyed() ? wc : null
  }

  // 控制服务 list 命令的标签摘要（viewport 取 guest 实际尺寸）
  summaries() {
    return Array.from(this.tabs.entries()).map(([tabId]) => this.summaryOf(tabId))
  }

  summaryOf(tabId) {
    const tab = this.tabs.get(tabId)
    if (!tab) return null
    let viewport = { width: 1280, height: 720 }
    const wc = webContents.fromId(tab.webContentsId)
    if (wc && !wc.isDestroyed()) {
      const [width, height] = wc.getSize()
      if (width > 0 && height > 0) viewport = { width, height }
    }
    return { tabId, url: tab.url, title: tab.title, active: tab.active, viewport }
  }
}

module.exports = { BrowserTabManager, BROWSER_PARTITION }
