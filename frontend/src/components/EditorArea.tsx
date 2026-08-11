import { forwardRef, useImperativeHandle, useState, useRef, useCallback, useEffect } from 'react'
import Tabs from './editor/Tabs'
import CodeEditor from './editor/CodeEditor'
import Terminal from './editor/Terminal'
import Resizer from './Resizer'

// 打开的标签页信息
interface OpenTab {
  path: string
  name: string
  content: string
  language: string
}

// 终端 tab 信息
interface TerminalTab {
  id: string       // 前端分配的实例 id
  title: string    // 显示名称
  ptyId?: string   // 后端 pty id，创建后填充
}

// 暴露给父组件的方法
export interface EditorAreaHandle {
  openFile: (path: string) => void
}

// EditorArea 的 props
interface EditorAreaProps {
  collapsed: boolean
  onToggleCollapse: () => void
}

// 生成唯一 id
const genId = () => `term-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`

const EditorArea = forwardRef<EditorAreaHandle, EditorAreaProps>(({ collapsed, onToggleCollapse }, ref) => {
  const [openTabs, setOpenTabs] = useState<OpenTab[]>([])
  const [activePath, setActivePath] = useState('')

  // 终端面板状态
  const [terminalVisible, setTerminalVisible] = useState(true)
  const [terminalHeight, setTerminalHeight] = useState(220)
  const [terminalTabs, setTerminalTabs] = useState<TerminalTab[]>(() => [
    { id: genId(), title: 'TERMINAL' },
  ])
  const [activeTerminalId, setActiveTerminalId] = useState<string>(() => terminalTabs[0].id)

  const openTabsRef = useRef<OpenTab[]>([])

  // Ctrl+` 切换终端面板
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.ctrlKey && e.key === '`') {
        e.preventDefault()
        setTerminalVisible((v) => !v)
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [])

  const updateTabs = useCallback((updater: (prev: OpenTab[]) => OpenTab[]) => {
    setOpenTabs((prev) => {
      const next = updater(prev)
      openTabsRef.current = next
      return next
    })
  }, [])

  // 打开文件：已打开则切换标签，否则请求内容后新增标签
  const openFile = useCallback(
    async (path: string) => {
      if (collapsed) {
        onToggleCollapse()
      }
      if (openTabsRef.current.some((t) => t.path === path)) {
        setActivePath(path)
        return
      }
      try {
        const res = await fetch(`/api/files/read?path=${encodeURIComponent(path)}`)
        const data = await res.json()
        const name = path.split('/').pop() || path
        updateTabs((prev) => [
          ...prev,
          { path, name, content: data.content, language: data.language },
        ])
        setActivePath(path)
      } catch (e) {
        console.error('读取文件失败', e)
      }
    },
    [updateTabs, collapsed, onToggleCollapse]
  )

  useImperativeHandle(ref, () => ({ openFile }), [openFile])

  // 关闭文件标签；关掉最后一个时自动收起编辑器（编辑器按需显示，不常驻）
  const handleClose = (path: string) => {
    const prev = openTabsRef.current
    const next = prev.filter((t) => t.path !== path)
    updateTabs(() => next)
    if (path === activePath) {
      const idx = prev.findIndex((t) => t.path === path)
      const neighbor = next[idx] || next[idx - 1]
      setActivePath(neighbor ? neighbor.path : '')
    }
    // 最后一个标签关闭且当前展开 → 自动收起
    if (next.length === 0 && !collapsed) {
      onToggleCollapse()
    }
  }

  const activeTab = openTabs.find((t) => t.path === activePath)

  // 新建终端 tab
  const addTerminal = useCallback(() => {
    const newTab: TerminalTab = { id: genId(), title: 'TERMINAL' }
    setTerminalTabs((prev) => [...prev, newTab])
    setActiveTerminalId(newTab.id)
  }, [])

  // 关闭终端 tab
  const closeTerminal = useCallback((id: string) => {
    setTerminalTabs((prev) => {
      const next = prev.filter((t) => t.id !== id)
      if (next.length === 0) {
        // 至少保留一个，或者隐藏面板
        const fresh = { id: genId(), title: 'TERMINAL' }
        setActiveTerminalId(fresh.id)
        return [fresh]
      }
      if (id === activeTerminalId) {
        setActiveTerminalId(next[next.length - 1].id)
      }
      return next
    })
  }, [activeTerminalId])

  // 终端拖拽调高度
  // 分隔条是终端的顶边界
  // 向下拖（delta 正）= 顶边界下移 = 终端变矮
  // 向上拖（delta 负）= 顶边界上移 = 终端变高
  // 分隔条跟着鼠标走，符合物理直觉
  const handleTerminalResize = useCallback((delta: number) => {
    setTerminalHeight((prev) => {
      return Math.max(80, Math.min(500, prev - delta))
    })
  }, [])

  // 终端创建完成回调
  const handleTerminalReady = useCallback((tabId: string, ptyId: string) => {
    setTerminalTabs((prev) =>
      prev.map((t) => (t.id === tabId ? { ...t, ptyId } : t))
    )
  }, [])

  // 折叠时：没有打开标签则完全不渲染（编辑器按需显示）；有标签才渲染展开窄条
  if (collapsed) {
    if (openTabs.length === 0) return null
    return (
      <div
        style={{
          backgroundColor: 'var(--bg-base)',
          borderLeft: '1px solid var(--border-subtle)',
          height: '100%',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          cursor: 'pointer',
          transition: 'background var(--transition-fast)',
        }}
        onClick={onToggleCollapse}
        title="展开编辑器"
        onMouseEnter={(e) => (e.currentTarget.style.background = 'var(--bg-tertiary)')}
        onMouseLeave={(e) => (e.currentTarget.style.background = 'var(--bg-base)')}
      >
        <span
          style={{
            color: 'var(--text-tertiary)',
            fontSize: '11px',
            writingMode: 'vertical-rl',
            letterSpacing: '1.5px',
            userSelect: 'none',
            fontFamily: 'var(--font-ui)',
            fontWeight: 500,
          }}
        >
          » 编辑器
        </span>
      </div>
    )
  }

  return (
    <div
      style={{
        backgroundColor: 'var(--bg-primary)',
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        overflow: 'hidden',
      }}
    >
      {/* 顶部标签栏 + 折叠按钮 */}
      <div
        style={{
          display: 'flex',
          alignItems: 'stretch',
          flexShrink: 0,
          backgroundColor: 'var(--bg-primary)',
          borderBottom: '1px solid var(--border)',
        }}
      >
        {openTabs.length > 0 && (
          <Tabs
            tabs={openTabs}
            activePath={activePath}
            onSwitch={setActivePath}
            onClose={handleClose}
          />
        )}
        <button
          onClick={onToggleCollapse}
          title="折叠编辑器"
          style={{
            border: 'none',
            background: 'transparent',
            color: 'var(--text-tertiary)',
            cursor: 'pointer',
            padding: '0 10px',
            marginLeft: 'auto',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            transition: 'all var(--transition-fast)',
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.background = 'var(--bg-tertiary)'
            e.currentTarget.style.color = 'var(--accent)'
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.background = 'transparent'
            e.currentTarget.style.color = 'var(--text-tertiary)'
          }}
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <path d="M9 6l6 6-6 6" />
          </svg>
        </button>
      </div>

      {/* 代码编辑区（无激活标签时不渲染空态，编辑器只在有文件时展示） */}
      {activeTab && (
        <div style={{ flex: 1, overflow: 'hidden' }}>
          <CodeEditor
            path={activeTab.path}
            content={activeTab.content}
            language={activeTab.language}
          />
        </div>
      )}

      {/* 底部终端面板 - 多 tab + 可调高度 */}
      {terminalVisible && (
        <>
          {/* 上方拖拽条 */}
          <Resizer direction="vertical" onResize={handleTerminalResize} />
          <div
            style={{
              height: terminalHeight,
              display: 'flex',
              flexDirection: 'column',
              borderTop: '1px solid var(--border)',
              flexShrink: 0,
              backgroundColor: 'var(--bg-base)',
            }}
          >
            {/* 终端 tab 栏 */}
            <div
              style={{
                height: '32px',
                display: 'flex',
                alignItems: 'stretch',
                backgroundColor: 'var(--bg-primary)',
                borderBottom: '1px solid var(--border-subtle)',
                flexShrink: 0,
              }}
            >
              {terminalTabs.map((tab, idx) => {
                const active = tab.id === activeTerminalId
                return (
                  <div
                    key={tab.id}
                    onClick={() => setActiveTerminalId(tab.id)}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: '6px',
                      padding: '0 12px',
                      cursor: 'pointer',
                      fontSize: '11px',
                      fontFamily: 'var(--font-mono)',
                      color: active ? 'var(--text-primary)' : 'var(--text-tertiary)',
                      backgroundColor: active ? 'var(--bg-base)' : 'transparent',
                      borderRight: '1px solid var(--border-subtle)',
                      borderBottom: active ? '2px solid var(--accent)' : '2px solid transparent',
                      whiteSpace: 'nowrap',
                      transition: 'all var(--transition-fast)',
                      letterSpacing: '0.5px',
                    }}
                    onMouseEnter={(e) => {
                      if (!active) {
                        e.currentTarget.style.color = 'var(--text-secondary)'
                        e.currentTarget.style.backgroundColor = 'var(--bg-tertiary)'
                      }
                    }}
                    onMouseLeave={(e) => {
                      if (!active) {
                        e.currentTarget.style.color = 'var(--text-tertiary)'
                        e.currentTarget.style.backgroundColor = 'transparent'
                      }
                    }}
                  >
                    <span>{tab.title} {idx + 1}</span>
                    {/* 关闭按钮 - 多于 1 个 tab 才显示 */}
                    {terminalTabs.length > 1 && (
                      <button
                        onClick={(e) => {
                          e.stopPropagation()
                          closeTerminal(tab.id)
                        }}
                        title="关闭终端"
                        style={{
                          border: 'none',
                          background: 'transparent',
                          color: 'var(--text-tertiary)',
                          cursor: 'pointer',
                          padding: '0',
                          width: '14px',
                          height: '14px',
                          borderRadius: '3px',
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'center',
                          transition: 'all var(--transition-fast)',
                        }}
                        onMouseEnter={(e) => {
                          e.currentTarget.style.background = 'var(--bg-elevated)'
                          e.currentTarget.style.color = 'var(--error)'
                        }}
                        onMouseLeave={(e) => {
                          e.currentTarget.style.background = 'transparent'
                          e.currentTarget.style.color = 'var(--text-tertiary)'
                        }}
                      >
                        ×
                      </button>
                    )}
                  </div>
                )
              })}
              {/* 新建终端按钮 */}
              <button
                onClick={addTerminal}
                title="新建终端"
                style={{
                  border: 'none',
                  background: 'transparent',
                  color: 'var(--text-tertiary)',
                  cursor: 'pointer',
                  padding: '0 10px',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  transition: 'all var(--transition-fast)',
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.background = 'var(--bg-tertiary)'
                  e.currentTarget.style.color = 'var(--accent)'
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.background = 'transparent'
                  e.currentTarget.style.color = 'var(--text-tertiary)'
                }}
              >
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M12 5v14M5 12h14" />
                </svg>
              </button>
              {/* 右侧隐藏按钮 */}
              <button
                onClick={() => setTerminalVisible(false)}
                title="隐藏终端 (Ctrl+`)"
                style={{
                  border: 'none',
                  background: 'transparent',
                  color: 'var(--text-tertiary)',
                  cursor: 'pointer',
                  padding: '0 10px',
                  marginLeft: 'auto',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  transition: 'all var(--transition-fast)',
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.background = 'var(--bg-tertiary)'
                  e.currentTarget.style.color = 'var(--text-primary)'
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.background = 'transparent'
                  e.currentTarget.style.color = 'var(--text-tertiary)'
                }}
              >
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M6 9l6 6 6-6" />
                </svg>
              </button>
            </div>
            {/* 终端内容区 - 只渲染当前激活的终端，切换 tab 重建 */}
            <div style={{ flex: 1, overflow: 'hidden' }}>
              <Terminal
                key={activeTerminalId}
                instanceId={activeTerminalId}
                onReady={(ptyId) => handleTerminalReady(activeTerminalId, ptyId)}
              />
            </div>
          </div>
        </>
      )}
    </div>
  )
})

EditorArea.displayName = 'EditorArea'

export default EditorArea
