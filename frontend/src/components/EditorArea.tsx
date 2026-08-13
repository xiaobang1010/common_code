import { forwardRef, useImperativeHandle, useState, useRef, useCallback, useEffect } from 'react'
import Tabs from './editor/Tabs'
import CodeEditor from './editor/CodeEditor'
import Terminal from './editor/Terminal'
import Resizer from './Resizer'
import { filesApi, type FileWriteError } from '../api/client'

// 打开的标签页信息
interface OpenTab {
  path: string
  name: string
  language: string
  bufferContent: string // 当前编辑缓冲内容
  diskContent: string   // 磁盘基线内容（dirty = buffer !== disk）
  baseMtime: number     // 打开/保存时的磁盘 mtime 基线（整数秒）
  baseSize: number      // 打开/保存时的磁盘 size 基线（字节）
  editable: boolean     // 是否可编辑（超大小上限为只读）
  saving: boolean       // 保存中
  error: string         // 保存/读取错误提示
  stale: boolean        // 磁盘已被外部（AI）修改，需重新加载
  revision: number      // 内容整体重置时 +1，用于触发编辑器重挂载
}

// 终端 tab 信息
interface TerminalTab {
  id: string       // 前端分配的实例 id
  title: string    // 显示名称
  ptyId?: string   // 后端 pty id，创建后填充
}

// 保存冲突弹窗信息
interface ConflictInfo {
  path: string
  currentMtime: number
  currentSize: number
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
  const [conflict, setConflict] = useState<ConflictInfo | null>(null)
  const [pendingClose, setPendingClose] = useState<string | null>(null)

  // 终端面板状态
  const [terminalVisible, setTerminalVisible] = useState(true)
  const [terminalHeight, setTerminalHeight] = useState(220)
  const [terminalTabs, setTerminalTabs] = useState<TerminalTab[]>(() => [
    { id: genId(), title: 'TERMINAL' },
  ])
  const [activeTerminalId, setActiveTerminalId] = useState<string>(() => terminalTabs[0].id)

  const openTabsRef = useRef<OpenTab[]>([])
  const activePathRef = useRef('')

  const setActive = useCallback((path: string) => {
    activePathRef.current = path
    setActivePath(path)
  }, [])

  const updateTabs = useCallback((updater: (prev: OpenTab[]) => OpenTab[]) => {
    setOpenTabs((prev) => {
      const next = updater(prev)
      openTabsRef.current = next
      return next
    })
  }, [])

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

  // 打开文件：已打开则切换标签，否则请求内容后新增标签
  const openFile = useCallback(
    async (path: string) => {
      if (collapsed) {
        onToggleCollapse()
      }
      if (openTabsRef.current.some((t) => t.path === path)) {
        setActive(path)
        return
      }
      try {
        const data = await filesApi.read(path)
        const name = path.split('/').pop() || path
        updateTabs((prev) => [
          ...prev,
          {
            path,
            name,
            language: data.language,
            bufferContent: data.content,
            diskContent: data.content,
            baseMtime: data.mtime,
            baseSize: data.size,
            editable: data.editable,
            saving: false,
            error: '',
            stale: false,
            revision: 0,
          },
        ])
        setActive(path)
      } catch (e) {
        console.error('读取文件失败', e)
      }
    },
    [updateTabs, setActive, collapsed, onToggleCollapse]
  )

  useImperativeHandle(ref, () => ({ openFile }), [openFile])

  // 编辑器内容变更：只更新缓冲，不回灌 value，保持 Monaco 自身 undo 栈
  const handleEditorChange = useCallback(
    (path: string, value: string) => {
      updateTabs((prev) =>
        prev.map((t) => (t.path === path && t.bufferContent !== value ? { ...t, bufferContent: value } : t))
      )
    },
    [updateTabs]
  )

  // 保存：带基线提交；成功同步基线并清 dirty，失败保留内容（冲突弹窗 / 错误提示）
  const saveFile = useCallback(
    async (path: string): Promise<boolean> => {
      const tab = openTabsRef.current.find((t) => t.path === path)
      if (!tab || !tab.editable || tab.saving) return false
      if (tab.bufferContent === tab.diskContent) return true

      updateTabs((prev) => prev.map((t) => (t.path === path ? { ...t, saving: true, error: '' } : t)))
      try {
        const result = await filesApi.write({
          path,
          content: tab.bufferContent,
          base_mtime: tab.baseMtime,
          base_size: tab.baseSize,
        })
        updateTabs((prev) =>
          prev.map((t) =>
            t.path === path
              ? { ...t, diskContent: t.bufferContent, baseMtime: result.mtime, baseSize: result.size, saving: false, error: '', stale: false }
              : t
          )
        )
        return true
      } catch (e) {
        const err = e as FileWriteError
        if (err.status === 409 && err.conflict) {
          updateTabs((prev) => prev.map((t) => (t.path === path ? { ...t, saving: false } : t)))
          setConflict({ path, currentMtime: err.conflict.current_mtime, currentSize: err.conflict.current_size })
        } else {
          updateTabs((prev) => prev.map((t) => (t.path === path ? { ...t, saving: false, error: err.message || '保存失败' } : t)))
        }
        return false
      }
    },
    [updateTabs]
  )

  // 覆盖磁盘版本：不带基线强制写入
  const forceSave = useCallback(
    async (path: string) => {
      const tab = openTabsRef.current.find((t) => t.path === path)
      if (!tab) return
      updateTabs((prev) => prev.map((t) => (t.path === path ? { ...t, saving: true } : t)))
      try {
        const result = await filesApi.write({ path, content: tab.bufferContent })
        updateTabs((prev) =>
          prev.map((t) =>
            t.path === path
              ? { ...t, diskContent: t.bufferContent, baseMtime: result.mtime, baseSize: result.size, saving: false, error: '', stale: false }
              : t
          )
        )
      } catch (e) {
        const err = e as FileWriteError
        updateTabs((prev) => prev.map((t) => (t.path === path ? { ...t, saving: false, error: err.message || '保存失败' } : t)))
      }
    },
    [updateTabs]
  )

  // 重新加载：读回磁盘最新内容并重置基线（放弃本地修改）
  const reloadTab = useCallback(
    async (path: string) => {
      try {
        const data = await filesApi.read(path)
        updateTabs((prev) =>
          prev.map((t) =>
            t.path === path
              ? {
                  ...t,
                  bufferContent: data.content,
                  diskContent: data.content,
                  baseMtime: data.mtime,
                  baseSize: data.size,
                  editable: data.editable,
                  stale: false,
                  error: '',
                  revision: t.revision + 1,
                }
              : t
          )
        )
      } catch (e) {
        console.error('重新加载失败', e)
      }
    },
    [updateTabs]
  )

  // 真正关闭 tab（无 dirty 或已处理）
  const doClose = useCallback(
    (path: string) => {
      const prev = openTabsRef.current
      const next = prev.filter((t) => t.path !== path)
      updateTabs(() => next)
      if (path === activePathRef.current) {
        const idx = prev.findIndex((t) => t.path === path)
        const neighbor = next[idx] || next[idx - 1]
        setActive(neighbor ? neighbor.path : '')
      }
      // 最后一个标签关闭且当前展开 → 自动收起
      if (next.length === 0 && !collapsed) {
        onToggleCollapse()
      }
    },
    [updateTabs, setActive, collapsed, onToggleCollapse]
  )

  // 关闭文件标签：dirty 时先询问
  const handleClose = (path: string) => {
    const tab = openTabsRef.current.find((t) => t.path === path)
    if (tab && tab.bufferContent !== tab.diskContent) {
      setPendingClose(path)
    } else {
      doClose(path)
    }
  }

  const handleCloseSave = async () => {
    const path = pendingClose
    if (!path) return
    const ok = await saveFile(path)
    setPendingClose(null)
    if (ok) {
      doClose(path)
    }
  }

  const handleCloseDiscard = () => {
    const path = pendingClose
    setPendingClose(null)
    if (path) doClose(path)
  }

  // 存在未保存修改时，刷新/关闭页面触发浏览器确认
  useEffect(() => {
    const handler = (e: BeforeUnloadEvent) => {
      if (openTabsRef.current.some((t) => t.bufferContent !== t.diskContent)) {
        e.preventDefault()
        e.returnValue = ''
      }
    }
    window.addEventListener('beforeunload', handler)
    return () => window.removeEventListener('beforeunload', handler)
  }, [])

  // Ctrl+S 保存当前激活 tab（拦截浏览器默认保存行为）
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.ctrlKey && e.key === 's') {
        e.preventDefault()
        const active = openTabsRef.current.find((t) => t.path === activePathRef.current)
        if (active && active.editable && active.bufferContent !== active.diskContent) {
          void saveFile(active.path)
        }
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [saveFile])

  // 订阅文件变更事件：AI 写盘后把打开的对应 tab 标记为过期
  useEffect(() => {
    const es = new EventSource('/api/files/events')
    es.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data)
        if (data.type === 'file_changed') {
          const path = data.path as string
          updateTabs((prev) => prev.map((t) => (t.path === path ? { ...t, stale: true } : t)))
        }
      } catch {
        // 忽略无法解析的事件
      }
    }
    return () => es.close()
  }, [updateTabs])

  const activeTab = openTabs.find((t) => t.path === activePath)
  const pendingCloseName = pendingClose ? openTabs.find((t) => t.path === pendingClose)?.name : ''

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

  const activeDirty = activeTab ? activeTab.bufferContent !== activeTab.diskContent : false

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
      {/* 顶部标签栏 + 保存按钮 + 折叠按钮 */}
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
            tabs={openTabs.map((t) => ({ path: t.path, name: t.name, dirty: t.bufferContent !== t.diskContent }))}
            activePath={activePath}
            onSwitch={setActive}
            onClose={handleClose}
          />
        )}
        {activeTab && (
          <button
            onClick={() => void saveFile(activeTab.path)}
            disabled={!activeDirty || activeTab.saving || !activeTab.editable}
            title="保存 (Ctrl+S)"
            style={{
              border: 'none',
              background: 'transparent',
              color: activeDirty ? 'var(--accent)' : 'var(--text-tertiary)',
              cursor: activeDirty && !activeTab.saving ? 'pointer' : 'default',
              padding: '0 10px',
              fontSize: '12px',
              fontFamily: 'var(--font-ui)',
              display: 'flex',
              alignItems: 'center',
              whiteSpace: 'nowrap',
              opacity: activeDirty && !activeTab.saving ? 1 : 0.45,
            }}
          >
            {activeTab.saving ? '保存中…' : '保存'}
          </button>
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
        <div style={{ flex: 1, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
          {activeTab.stale && (
            <div
              style={{
                padding: '6px 12px',
                backgroundColor: 'var(--bg-elevated)',
                color: 'var(--warning)',
                fontSize: '12px',
                fontFamily: 'var(--font-ui)',
                display: 'flex',
                alignItems: 'center',
                gap: '10px',
              }}
            >
              {activeTab.bufferContent !== activeTab.diskContent ? (
                <span>文件已被 AI 修改，你有未保存更改。</span>
              ) : (
                <>
                  <span>磁盘已变更，可能由 AI 更新。</span>
                  <button
                    onClick={() => void reloadTab(activeTab.path)}
                    style={{ border: 'none', background: 'transparent', color: 'var(--accent)', cursor: 'pointer', fontSize: '12px', padding: 0 }}
                  >
                    点击重新加载
                  </button>
                </>
              )}
            </div>
          )}
          {!activeTab.editable && (
            <div
              style={{
                padding: '6px 12px',
                backgroundColor: 'var(--bg-elevated)',
                color: 'var(--text-tertiary)',
                fontSize: '12px',
                fontFamily: 'var(--font-ui)',
              }}
            >
              文件过大，仅支持查看
            </div>
          )}
          {activeTab.error && (
            <div
              style={{
                padding: '6px 12px',
                backgroundColor: 'var(--bg-elevated)',
                color: 'var(--error)',
                fontSize: '12px',
                fontFamily: 'var(--font-ui)',
              }}
            >
              {activeTab.error}
            </div>
          )}
          <div style={{ flex: 1, overflow: 'hidden' }}>
            <CodeEditor
              key={`${activeTab.path}:${activeTab.revision}`}
              content={activeTab.bufferContent}
              language={activeTab.language}
              readOnly={!activeTab.editable}
              onChange={(value) => handleEditorChange(activeTab.path, value)}
            />
          </div>
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

      {/* 保存冲突弹窗 */}
      {conflict && (
        <div
          style={{
            position: 'fixed',
            inset: 0,
            backgroundColor: 'rgba(0,0,0,0.5)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 100,
          }}
          onClick={() => setConflict(null)}
        >
          <div
            style={{
              backgroundColor: 'var(--bg-elevated)',
              border: '1px solid var(--border)',
              borderRadius: 'var(--radius-md)',
              padding: '20px',
              maxWidth: '420px',
              display: 'flex',
              flexDirection: 'column',
              gap: '14px',
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <div style={{ color: 'var(--text-primary)', fontSize: '13px', fontFamily: 'var(--font-ui)' }}>
              文件已在磁盘被修改，可能由 AI 更新。
              <br />
              你的修改尚未写入，请选择如何处理。
            </div>
            <div style={{ display: 'flex', gap: '10px', justifyContent: 'flex-end' }}>
              <button
                onClick={() => {
                  const c = conflict
                  setConflict(null)
                  void forceSave(c.path)
                }}
                style={{ padding: '6px 12px', cursor: 'pointer', background: 'var(--accent)', color: '#fff', border: 'none', borderRadius: 'var(--radius-sm)', fontSize: '12px' }}
              >
                覆盖磁盘版本
              </button>
              <button
                onClick={() => {
                  const c = conflict
                  setConflict(null)
                  void reloadTab(c.path)
                }}
                style={{ padding: '6px 12px', cursor: 'pointer', background: 'transparent', color: 'var(--text-primary)', border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)', fontSize: '12px' }}
              >
                放弃修改并重新加载
              </button>
              <button
                onClick={() => setConflict(null)}
                style={{ padding: '6px 12px', cursor: 'pointer', background: 'transparent', color: 'var(--text-secondary)', border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)', fontSize: '12px' }}
              >
                取消
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 关闭未保存 tab 弹窗 */}
      {pendingClose && (
        <div
          style={{
            position: 'fixed',
            inset: 0,
            backgroundColor: 'rgba(0,0,0,0.5)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 100,
          }}
          onClick={() => setPendingClose(null)}
        >
          <div
            style={{
              backgroundColor: 'var(--bg-elevated)',
              border: '1px solid var(--border)',
              borderRadius: 'var(--radius-md)',
              padding: '20px',
              maxWidth: '420px',
              display: 'flex',
              flexDirection: 'column',
              gap: '14px',
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <div style={{ color: 'var(--text-primary)', fontSize: '13px', fontFamily: 'var(--font-ui)' }}>
              「{pendingCloseName}」有未保存的修改。
              <br />
              要保存这些修改吗？
            </div>
            <div style={{ display: 'flex', gap: '10px', justifyContent: 'flex-end' }}>
              <button
                onClick={() => void handleCloseSave()}
                style={{ padding: '6px 12px', cursor: 'pointer', background: 'var(--accent)', color: '#fff', border: 'none', borderRadius: 'var(--radius-sm)', fontSize: '12px' }}
              >
                保存
              </button>
              <button
                onClick={handleCloseDiscard}
                style={{ padding: '6px 12px', cursor: 'pointer', background: 'transparent', color: 'var(--text-primary)', border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)', fontSize: '12px' }}
              >
                不保存
              </button>
              <button
                onClick={() => setPendingClose(null)}
                style={{ padding: '6px 12px', cursor: 'pointer', background: 'transparent', color: 'var(--text-secondary)', border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)', fontSize: '12px' }}
              >
                取消
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
})

EditorArea.displayName = 'EditorArea'

export default EditorArea
