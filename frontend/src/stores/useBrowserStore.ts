import { create } from 'zustand'

// 内置浏览器网页标签状态：放在全局 store 而非 BrowserPane 组件内，
// 因为产物区折叠时 BrowserPane 不挂载，而主进程命令（agent 建标签）需要
// 常驻的 App 层先接收、写入 store，再由随后挂载的 BrowserPane 渲染并回报。

export interface BrowserTab {
  tabId: string
  url: string
  title: string
  // 建标签时捕获的导航 URL：webview 的 src 必须是一次性常量。若 src 绑活 url，
  // 导航 commit 后 store 更新会重写 src 属性，触发对同一 URL 的第二次 loadURL，
  // 打断进行中的导航（agent goto 收到 ERR_ABORTED）。url 字段继续随导航更新
  initialUrl: string
}

interface BrowserState {
  tabs: BrowserTab[]
  activeTabId: string | null
  // 导航事件计数：webview 历史状态（canGoBack/Forward）不在 store 里，
  // 用它驱动工具栏按钮可用性重算
  historyTick: number
  bump: () => void
  // 确保存在指定（或自动新建）标签并激活，返回 tabId：agent tab-add 与手动「+」共用
  ensureTab: (tabId?: string, url?: string) => string
  closeTab: (tabId: string) => void
  activateTab: (tabId: string) => void
  updateTab: (tabId: string, patch: Partial<Omit<BrowserTab, 'tabId'>>) => void
  // 回收：浏览器工具标签关闭/折叠时清空（webview 元素随之卸载、guest 销毁）
  reset: () => void
}

// 渲染侧手动新建的 tabId 前缀，与主进程 agent 发起的 a- 前缀区分
let rendererCounter = 0

export const useBrowserStore = create<BrowserState>((set, get) => ({
  tabs: [],
  activeTabId: null,
  historyTick: 0,
  bump: () => set((s) => ({ historyTick: s.historyTick + 1 })),

  ensureTab: (tabId, url) => {
    const id = tabId ?? `r-${++rendererCounter}`
    const existing = get().tabs.find((t) => t.tabId === id)
    if (existing) {
      set({ activeTabId: id })
    } else {
      set((s) => ({
        tabs: [...s.tabs, { tabId: id, url: url ?? '', initialUrl: url ?? '', title: url || '新标签' }],
        activeTabId: id,
      }))
    }
    return id
  },

  closeTab: (tabId) => {
    set((s) => {
      const tabs = s.tabs.filter((t) => t.tabId !== tabId)
      let active = s.activeTabId
      if (active === tabId) active = tabs.length ? tabs[tabs.length - 1].tabId : null
      return { tabs, activeTabId: active }
    })
  },

  activateTab: (tabId) => {
    if (get().tabs.some((t) => t.tabId === tabId)) set({ activeTabId: tabId })
  },

  updateTab: (tabId, patch) => {
    set((s) => ({
      tabs: s.tabs.map((t) => (t.tabId === tabId ? { ...t, ...patch } : t)),
    }))
  },

  reset: () => set({ tabs: [], activeTabId: null }),
}))
