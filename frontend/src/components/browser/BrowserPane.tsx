import { useCallback, useEffect, useRef, useState } from 'react'
import { useBrowserStore } from '../../stores/useBrowserStore'
import {
  reportTabCreated,
  reportTabState,
  reportTabActive,
  reportTabClosed,
  reportTeardown,
} from '../../utils/browserBridge'
import { normalizeAddress } from '../../utils/browserLogic'

// @types/react 内置 webview 标签类型（HTMLWebViewElement 为空壳），
// 经声明合并补上 Electron guest 元素实际可用的 API 面
declare global {
  interface HTMLWebViewElement extends HTMLElement {
    getURL(): string
    getTitle(): string
    canGoBack(): boolean
    canGoForward(): boolean
    loadURL(url: string): Promise<void>
    goBack(): void
    goForward(): void
    reload(): void
    openDevTools(): void
    closeDevTools(): void
    getWebContentsId(): number
  }
}

type WebviewEl = HTMLWebViewElement

// 固定持久化分区：与主进程约定一致，cookie/登录态跨会话保留
const BROWSER_PARTITION = 'persist:inapp-browser'

// 工具栏按钮统一样式
const toolBtnStyle: React.CSSProperties = {
  border: 'none',
  background: 'transparent',
  color: 'var(--text-secondary)',
  cursor: 'pointer',
  fontSize: '14px',
  padding: '2px 6px',
  borderRadius: 'var(--radius-sm)',
  lineHeight: 1,
  fontFamily: 'var(--font-ui)',
}

// 单个网页标签的 webview 帧：挂载后回报 guest id，导航/标题变化同步 store 与主进程
function WebviewFrame({
  tabId,
  initialUrl,
  active,
}: {
  tabId: string
  initialUrl: string
  active: boolean
}) {
  const elRef = useRef<WebviewEl | null>(null)
  const updateTab = useBrowserStore((s) => s.updateTab)
  const bump = useBrowserStore((s) => s.bump)

  const syncState = useCallback(() => {
    const el = elRef.current
    if (!el) return
    const url = el.getURL?.() ?? ''
    const title = el.getTitle?.() ?? ''
    updateTab(tabId, { url, title })
    reportTabState(tabId, url, title)
    bump() // 驱动工具栏历史按钮可用性刷新
  }, [tabId, updateTab, bump])

  useEffect(() => {
    const el = elRef.current
    if (!el) return
    const handlers: Array<[string, () => void]> = [
      ['dom-ready', () => {
        // guest 就绪后才有 webContentsId，回报主进程完成注册/握手
        try {
          reportTabCreated(tabId, el.getWebContentsId(), el.getURL?.() ?? '', el.getTitle?.() ?? '', active)
        } catch { /* 元素已销毁 */ }
      }],
      ['did-navigate', syncState],
      ['did-navigate-in-page', syncState],
      ['page-title-updated', syncState],
      ['did-stop-loading', syncState],
    ]
    for (const [ev, fn] of handlers) el.addEventListener(ev, fn)
    return () => {
      for (const [ev, fn] of handlers) el.removeEventListener(ev, fn)
    }
  }, [tabId, active, syncState])

  return (
    <webview
      data-tab-id={tabId}
      ref={(el) => {
        elRef.current = el as WebviewEl | null
      }}
      partition={BROWSER_PARTITION}
      src={initialUrl || 'about:blank'}
      style={{
        display: active ? 'flex' : 'none',
        flex: 1,
        width: '100%',
        height: '100%',
        border: 'none',
        backgroundColor: '#ffffff',
      }}
    />
  )
}

// 浏览器工具标签内容：网页标签条 + 导航工具栏 + webview 区 / 空态
export default function BrowserPane() {
  const tabs = useBrowserStore((s) => s.tabs)
  const activeTabId = useBrowserStore((s) => s.activeTabId)
  const historyTick = useBrowserStore((s) => s.historyTick)
  const ensureTab = useBrowserStore((s) => s.ensureTab)
  const closeTab = useBrowserStore((s) => s.closeTab)
  const activateTab = useBrowserStore((s) => s.activateTab)

  const [address, setAddress] = useState('')
  const [devToolsOpen, setDevToolsOpen] = useState(false)
  // 地址栏只在切换标签或页面导航时同步，用户输入中途不被覆盖
  const activeTab = tabs.find((t) => t.tabId === activeTabId) ?? null
  useEffect(() => {
    setAddress(activeTab?.url ?? '')
  }, [activeTabId, activeTab?.url])

  // 卸载即回收（双路之一，与 App 层「状态移出」effect 幂等互补）：
  // 产物区折叠时本组件随 ArtifactPanel 早退卸载，主进程注册表与 guest 一并清账
  useEffect(() => () => reportTeardown(), [])

  const activeEl = (): WebviewEl | null =>
    document.querySelector<WebviewEl>(`webview[data-tab-id="${activeTabId}"]`)

  const canBack = historyTick >= 0 && !!activeEl()?.canGoBack?.()
  const canFwd = historyTick >= 0 && !!activeEl()?.canGoForward?.()

  const navigate = (url: string) => {
    const el = activeEl()
    if (!el) return
    const target = normalizeAddress(url)
    if (target) void el.loadURL(target)
  }

  const handleClose = (tabId: string) => {
    reportTabClosed(tabId)
    closeTab(tabId)
  }

  const handleActivate = (tabId: string) => {
    activateTab(tabId)
    reportTabActive(tabId)
  }

  return (
    <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}>
      {/* 网页标签条 */}
      {tabs.length > 0 && (
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '4px',
            padding: '4px 6px 0',
            borderBottom: '1px solid var(--border-subtle)',
            flexShrink: 0,
            overflow: 'hidden',
          }}
        >
          {tabs.map((t) => (
            <div
              key={t.tabId}
              onClick={() => handleActivate(t.tabId)}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '6px',
                padding: '4px 8px',
                borderRadius: 'var(--radius-sm) var(--radius-sm) 0 0',
                backgroundColor: t.tabId === activeTabId ? 'var(--bg-tertiary)' : 'transparent',
                color: t.tabId === activeTabId ? 'var(--text-primary)' : 'var(--text-tertiary)',
                cursor: 'pointer',
                fontSize: '12px',
                fontFamily: 'var(--font-ui)',
                maxWidth: '160px',
                userSelect: 'none',
              }}
            >
              <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {t.title || t.url || '新标签'}
              </span>
              <button
                onClick={(e) => {
                  e.stopPropagation()
                  handleClose(t.tabId)
                }}
                title="关闭标签"
                style={{ ...toolBtnStyle, fontSize: '12px', padding: '0 2px' }}
              >
                ×
              </button>
            </div>
          ))}
          <button
            onClick={() => ensureTab()}
            title="新建标签"
            style={{ ...toolBtnStyle, fontSize: '15px' }}
          >
            +
          </button>
        </div>
      )}

      {/* 导航工具栏 */}
      {tabs.length > 0 && (
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '2px',
            padding: '5px 8px',
            borderBottom: '1px solid var(--border-subtle)',
            flexShrink: 0,
          }}
        >
          <button
            disabled={!canBack}
            onClick={() => activeEl()?.goBack()}
            title="后退"
            style={{ ...toolBtnStyle, opacity: canBack ? 1 : 0.35 }}
          >
            ‹
          </button>
          <button
            disabled={!canFwd}
            onClick={() => activeEl()?.goForward()}
            title="前进"
            style={{ ...toolBtnStyle, opacity: canFwd ? 1 : 0.35 }}
          >
            ›
          </button>
          <button
            onClick={() => activeEl()?.reload()}
            title="刷新"
            style={toolBtnStyle}
          >
            ⟳
          </button>
          <input
            value={address}
            onChange={(e) => setAddress(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && address.trim()) navigate(address)
            }}
            placeholder="输入网址后回车"
            spellCheck={false}
            style={{
              flex: 1,
              minWidth: 0,
              margin: '0 6px',
              padding: '4px 10px',
              border: '1px solid var(--border-subtle)',
              borderRadius: '999px',
              backgroundColor: 'var(--bg-secondary)',
              color: 'var(--text-primary)',
              fontSize: '12px',
              fontFamily: 'var(--font-ui)',
              outline: 'none',
            }}
          />
          <button
            onClick={() => {
              const el = activeEl()
              if (!el) return
              // devtools 无同步状态可读，用本地开关量实现开/关切换
              setDevToolsOpen((prev) => {
                try {
                  if (prev) el.closeDevTools()
                  else el.openDevTools()
                } catch { /* guest 已销毁等场景忽略 */ }
                return !prev
              })
            }}
            title="开发者工具"
            style={toolBtnStyle}
          >
            ⚙
          </button>
        </div>
      )}

      {/* webview 区 / 空态 */}
      {tabs.length === 0 ? (
        <div
          style={{
            flex: 1,
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            justifyContent: 'center',
            gap: '10px',
            color: 'var(--text-tertiary)',
            userSelect: 'none',
          }}
        >
          <svg width="44" height="44" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.2" opacity="0.6">
            <circle cx="12" cy="12" r="9" />
            <path d="M3 12h18" />
            <path d="M12 3a14 14 0 0 1 0 18a14 14 0 0 1 0-18" />
          </svg>
          <span style={{ fontSize: '13px', fontWeight: 500, color: 'var(--text-secondary)', fontFamily: 'var(--font-ui)' }}>浏览器</span>
          <span style={{ fontSize: '12px', fontFamily: 'var(--font-ui)' }}>粘贴或输入 URL 以打开网页。</span>
        </div>
      ) : (
        <div style={{ flex: 1, minHeight: 0, position: 'relative' }}>
          {tabs.map((t) => (
            <WebviewFrame
              key={t.tabId}
              tabId={t.tabId}
              initialUrl={t.url}
              active={t.tabId === activeTabId}
            />
          ))}
        </div>
      )}
    </div>
  )
}
