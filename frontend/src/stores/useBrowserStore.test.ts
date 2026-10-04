import { beforeEach, describe, expect, it } from 'vitest'
import { useBrowserStore } from './useBrowserStore'

// BrowserPane 的标签生命周期状态机在 store 中（组件常挂载、状态跨折叠/重建保留），
// 对 store 做纯逻辑断言：新建/激活/关闭迁活/更新/回收。

describe('useBrowserStore：网页标签状态机', () => {
  beforeEach(() => {
    useBrowserStore.getState().reset()
  })

  it('ensureTab 无 id 时自动新建并激活', () => {
    const id = useBrowserStore.getState().ensureTab(undefined, 'https://example.com')
    const s = useBrowserStore.getState()
    expect(s.tabs).toHaveLength(1)
    expect(s.tabs[0].tabId).toBe(id)
    expect(s.activeTabId).toBe(id)
    expect(s.tabs[0].url).toBe('https://example.com')
  })

  it('ensureTab 命中已有 id（agent tab-add 重放）只切换激活不重复建', () => {
    const id = useBrowserStore.getState().ensureTab('a-1', 'https://a.com')
    useBrowserStore.getState().ensureTab(undefined, 'https://b.com')
    useBrowserStore.getState().ensureTab(id)
    const s = useBrowserStore.getState()
    expect(s.tabs).toHaveLength(2)
    expect(s.activeTabId).toBe(id)
  })

  it('closeTab 关闭激活标签后迁到剩余最后一个，关空则激活为 null', () => {
    const id1 = useBrowserStore.getState().ensureTab('a-1')
    const id2 = useBrowserStore.getState().ensureTab('a-2')
    useBrowserStore.getState().closeTab(id2)
    expect(useBrowserStore.getState().activeTabId).toBe(id1)
    useBrowserStore.getState().closeTab(id1)
    const s = useBrowserStore.getState()
    expect(s.tabs).toHaveLength(0)
    expect(s.activeTabId).toBeNull()
  })

  it('activateTab 忽略未知 id', () => {
    const id = useBrowserStore.getState().ensureTab('a-1')
    useBrowserStore.getState().activateTab('nope')
    expect(useBrowserStore.getState().activeTabId).toBe(id)
  })

  it('updateTab 同步导航后的 url/title', () => {
    const id = useBrowserStore.getState().ensureTab('a-1')
    useBrowserStore.getState().updateTab(id, { url: 'https://x.com', title: 'X' })
    const t = useBrowserStore.getState().tabs[0]
    expect(t.url).toBe('https://x.com')
    expect(t.title).toBe('X')
  })

  it('reset 清空全部标签（回收语义）', () => {
    useBrowserStore.getState().ensureTab('a-1')
    useBrowserStore.getState().ensureTab('a-2')
    useBrowserStore.getState().reset()
    const s = useBrowserStore.getState()
    expect(s.tabs).toHaveLength(0)
    expect(s.activeTabId).toBeNull()
  })
})
