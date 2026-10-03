// 内置浏览器渲染侧桥：封装 preload 暴露的 electronAPI.browser，
// 纯浏览器环境（vite dev 直接开页面、无 Electron）下全部退化为空操作，组件不崩。

export interface BrowserCommand {
  type: 'tab-add' | 'tab-remove' | 'tab-activate' | 'pane-visibility'
  tabId?: string
  url?: string
  visible?: boolean
}

interface ElectronBrowserAPI {
  tabCreated: (info: { tabId: string; webContentsId: number; url?: string; title?: string; active?: boolean }) => void
  tabState: (info: { tabId: string; url?: string; title?: string }) => void
  tabActive: (tabId: string) => void
  tabClosed: (tabId: string) => void
  teardown: () => void
  visibility: (visible: boolean) => void
  onCommand: (callback: (cmd: BrowserCommand) => void) => () => void
}

function getBridge(): ElectronBrowserAPI | null {
  const w = window as unknown as { electronAPI?: { browser?: ElectronBrowserAPI } }
  return w.electronAPI?.browser ?? null
}

// 标签建好（拿到 guest webContentsId）后回报主进程注册表
export function reportTabCreated(tabId: string, webContentsId: number, url: string, title: string, active: boolean): void {
  getBridge()?.tabCreated({ tabId, webContentsId, url, title, active })
}

export function reportTabState(tabId: string, url: string, title: string): void {
  getBridge()?.tabState({ tabId, url, title })
}

export function reportTabActive(tabId: string): void {
  getBridge()?.tabActive(tabId)
}

export function reportTabClosed(tabId: string): void {
  getBridge()?.tabClosed(tabId)
}

// 浏览器工具标签被移出/面板卸载：通知主进程销毁 guest 并清空注册表
export function reportTeardown(): void {
  getBridge()?.teardown()
}

// 面板可见性变化（browser 标签是否处于激活显示状态）
export function reportVisibility(visible: boolean): void {
  getBridge()?.visibility(visible)
}

// 订阅主进程命令；无桥时返回空清理函数
export function onBrowserCommand(callback: (cmd: BrowserCommand) => void): () => void {
  return getBridge()?.onCommand(callback) ?? (() => {})
}
