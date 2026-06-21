import { forwardRef, useImperativeHandle, useState, useRef, useCallback, useEffect } from 'react'
import Tabs from './editor/Tabs'
import CodeEditor from './editor/CodeEditor'
import Terminal from './editor/Terminal'

// 打开的标签页信息
interface OpenTab {
  path: string
  name: string
  content: string
  language: string
}

// 暴露给父组件的方法
export interface EditorAreaHandle {
  openFile: (path: string) => void
}

const EditorArea = forwardRef<EditorAreaHandle>((_, ref) => {
  const [openTabs, setOpenTabs] = useState<OpenTab[]>([])
  const [activePath, setActivePath] = useState('')
  // 终端面板是否可见
  const [terminalVisible, setTerminalVisible] = useState(true)
  // 用 ref 跟踪已打开的标签，避免闭包读到旧值
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
    [updateTabs]
  )

  useImperativeHandle(ref, () => ({ openFile }), [openFile])

  // 关闭标签：若关的是激活标签，则切到相邻标签
  const handleClose = (path: string) => {
    updateTabs((prev) => {
      const next = prev.filter((t) => t.path !== path)
      if (path === activePath) {
        const idx = prev.findIndex((t) => t.path === path)
        const neighbor = next[idx] || next[idx - 1]
        setActivePath(neighbor ? neighbor.path : '')
      }
      return next
    })
  }

  const activeTab = openTabs.find((t) => t.path === activePath)

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
      {openTabs.length > 0 && (
        <Tabs
          tabs={openTabs}
          activePath={activePath}
          onSwitch={setActivePath}
          onClose={handleClose}
        />
      )}
      {activeTab ? (
        <div style={{ flex: 1, overflow: 'hidden' }}>
          <CodeEditor
            path={activeTab.path}
            content={activeTab.content}
            language={activeTab.language}
          />
        </div>
      ) : (
        <div
          style={{
            flex: 1,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            color: 'var(--text-secondary)',
            fontSize: '14px',
          }}
        >
          Common Code — 选择文件查看内容
        </div>
      )}
      {/* 底部终端面板，可折叠，Ctrl+` 切换 */}
      {terminalVisible && (
        <div
          style={{
            height: '220px',
            display: 'flex',
            flexDirection: 'column',
            borderTop: '1px solid var(--border)',
            flexShrink: 0,
          }}
        >
          {/* 终端标题栏，点击 × 可隐藏 */}
          <div
            style={{
              height: '30px',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              padding: '0 8px',
              backgroundColor: 'var(--bg-secondary)',
              borderBottom: '1px solid var(--border)',
              flexShrink: 0,
            }}
          >
            <span
              style={{
                fontSize: '11px',
                textTransform: 'uppercase',
                color: 'var(--text-secondary)',
              }}
            >
              终端
            </span>
            <button
              onClick={() => setTerminalVisible(false)}
              title="隐藏终端 (Ctrl+`)"
              style={{
                border: 'none',
                background: 'transparent',
                color: 'var(--text-secondary)',
                cursor: 'pointer',
                fontSize: '14px',
                padding: '2px 4px',
              }}
            >
              ×
            </button>
          </div>
          {/* 终端内容区 */}
          <div style={{ flex: 1, overflow: 'hidden' }}>
            <Terminal />
          </div>
        </div>
      )}
    </div>
  )
})

EditorArea.displayName = 'EditorArea'

export default EditorArea
