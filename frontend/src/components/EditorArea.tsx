import { forwardRef, useImperativeHandle, useState, useRef, useCallback, useEffect } from 'react'
import type { ReactNode } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import Tabs from './editor/Tabs'
import Breadcrumb from './editor/Breadcrumb'
import CodeEditor from './editor/CodeEditor'
import Terminal from './editor/Terminal'
import Resizer from './Resizer'
import FileTree from './sidebar/FileTree'
import SearchPanel from './sidebar/SearchPanel'
import SummaryCard from './inspector/cards/SummaryCard'
import ReviewCard from './inspector/cards/ReviewCard'
import QuickOpen from './editor/QuickOpen'
import { markdownComponents } from './ai/WorkBlock'
import { useChatStore } from '../stores/useChatStore'
import { TOOL_META, type ToolId } from './editor/toolMeta'
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

// 终端会话信息
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
  // 当前工作区路径，null 表示未选择工作区（树列显示占位提示用）
  workspacePath: string | null
  // 工具标签状态由 App 持有（标题栏开关/图标轨/快捷键共用）
  toolTabsOpen: ToolId[]
  activeToolId: ToolId | null
  onOpenTool: (id: ToolId) => void
  onCloseTool: (id: ToolId) => void
  // 文件标签被激活（点击文件标签/打开文件）时清掉工具标签激活态
  onActivateFile: () => void
}

// 生成唯一 id
const genId = () => `term-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`

// 终端工具标签内容：多会话 tab + 新建，关闭工具标签只隐藏组件不卸载（会话保活）
function TerminalToolContent() {
  const [tabs, setTabs] = useState<TerminalTab[]>(() => [{ id: genId(), title: 'TERMINAL' }])
  const [activeId, setActiveId] = useState<string>(() => tabs[0].id)

  const addTerminal = useCallback(() => {
    const newTab: TerminalTab = { id: genId(), title: 'TERMINAL' }
    setTabs((prev) => [...prev, newTab])
    setActiveId(newTab.id)
  }, [])

  const closeTerminal = useCallback((id: string) => {
    setTabs((prev) => {
      const next = prev.filter((t) => t.id !== id)
      if (next.length === 0) {
        // 至少保留一个会话
        const fresh = { id: genId(), title: 'TERMINAL' }
        setActiveId(fresh.id)
        return [fresh]
      }
      if (id === activeId) {
        setActiveId(next[next.length - 1].id)
      }
      return next
    })
  }, [activeId])

  const handleReady = useCallback((tabId: string, ptyId: string) => {
    setTabs((prev) => prev.map((t) => (t.id === tabId ? { ...t, ptyId } : t)))
  }, [])

  return (
    <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}>
      {/* 会话列表栏 */}
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
        {tabs.map((tab, idx) => {
          const active = tab.id === activeId
          return (
            <div
              key={tab.id}
              onClick={() => setActiveId(tab.id)}
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
              {/* 关闭按钮 - 多于 1 个会话才显示 */}
              {tabs.length > 1 && (
                <button
                  onClick={(e) => {
                    e.stopPropagation()
                    closeTerminal(tab.id)
                  }}
                  title="关闭终端会话"
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
        {/* 新建终端会话 */}
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
      </div>
      {/* 终端内容区 - 只渲染当前激活的会话，切换重建 */}
      <div style={{ flex: 1, overflow: 'hidden' }}>
        <Terminal key={activeId} instanceId={activeId} onReady={(ptyId) => handleReady(activeId, ptyId)} />
      </div>
    </div>
  )
}

const EditorArea = forwardRef<EditorAreaHandle, EditorAreaProps>(
  ({ collapsed, onToggleCollapse, workspacePath, toolTabsOpen, activeToolId, onOpenTool, onCloseTool, onActivateFile }, ref) => {
    const [openTabs, setOpenTabs] = useState<OpenTab[]>([])
    const [activePath, setActivePath] = useState('')
    const [conflict, setConflict] = useState<ConflictInfo | null>(null)
    const [pendingClose, setPendingClose] = useState<string | null>(null)
    // .md 预览模式：切文件时回到源码态
    const [previewMode, setPreviewMode] = useState(false)
    // 快速打开（Ctrl+P）
    const [quickOpen, setQuickOpen] = useState(false)
    // 最近打开的文件（会话内前端内存记录，重启不持久化）
    const [recentFiles, setRecentFiles] = useState<string[]>([])

    // 右缘文件树窄列：默认 220px，可拖拽 160-320px，可折叠（持久化）
    const [treeWidth, setTreeWidth] = useState(() => {
      const v = Number(localStorage.getItem('layout.treeWidth'))
      return v >= 160 && v <= 320 ? v : 220
    })
    const [treeCollapsed, setTreeCollapsed] = useState(() => localStorage.getItem('layout.treeCollapsed') === '1')

    const openTabsRef = useRef<OpenTab[]>([])
    const activePathRef = useRef('')
    const activeToolIdRef = useRef<ToolId | null>(activeToolId)
    const onActivateFileRef = useRef(onActivateFile)
    const onOpenToolRef = useRef(onOpenTool)
    const onCloseToolRef = useRef(onCloseTool)

    // 回调与激活态用 ref 保存，快捷键/树点击等长驻监听不因重渲失效
    useEffect(() => {
      activeToolIdRef.current = activeToolId
      onActivateFileRef.current = onActivateFile
      onOpenToolRef.current = onOpenTool
      onCloseToolRef.current = onCloseTool
    }, [activeToolId, onActivateFile, onOpenTool, onCloseTool])

    // 树列状态持久化
    useEffect(() => {
      localStorage.setItem('layout.treeWidth', String(treeWidth))
    }, [treeWidth])
    useEffect(() => {
      localStorage.setItem('layout.treeCollapsed', treeCollapsed ? '1' : '0')
    }, [treeCollapsed])

    // 「恢复默认布局」重置事件
    useEffect(() => {
      const handler = () => {
        setTreeWidth(220)
        setTreeCollapsed(false)
      }
      window.addEventListener('layout-reset', handler)
      return () => window.removeEventListener('layout-reset', handler)
    }, [])

    // 窄屏退让：编辑区自身宽度不足 640px 自动折叠树列；窗口不足 1000px 树列自动折叠
    const contentAreaRef = useRef<HTMLDivElement>(null)
    useEffect(() => {
      const el = contentAreaRef.current
      const foldTree = () => {
        if (window.innerWidth < 1000) setTreeCollapsed(true)
      }
      window.addEventListener('resize', foldTree)
      let ro: ResizeObserver | null = null
      if (el) {
        ro = new ResizeObserver(() => {
          if (el.clientWidth < 640) setTreeCollapsed(true)
        })
        ro.observe(el)
      }
      return () => {
        window.removeEventListener('resize', foldTree)
        ro?.disconnect()
      }
    }, [])

    // 激活文件标签：同时清掉工具标签激活态（内容区回到文件视图）
    const setActive = useCallback((path: string) => {
      activePathRef.current = path
      setActivePath(path)
      onActivateFileRef.current()
    }, [])

    const updateTabs = useCallback((updater: (prev: OpenTab[]) => OpenTab[]) => {
      setOpenTabs((prev) => {
        const next = updater(prev)
        openTabsRef.current = next
        return next
      })
    }, [])

    // Ctrl+` 唤起/收起终端工具标签（收起只隐藏，会话保活）
    useEffect(() => {
      const handler = (e: KeyboardEvent) => {
        if (e.ctrlKey && e.key === '`') {
          e.preventDefault()
          if (activeToolIdRef.current === 'terminal') {
            onCloseToolRef.current('terminal')
          } else {
            onOpenToolRef.current('terminal')
          }
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
        // 记录最近打开（去重置顶，最多 10 条）
        setRecentFiles((prev) => [path, ...prev.filter((p) => p !== path)].slice(0, 10))
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

    // Ctrl+P 快速打开文件
    useEffect(() => {
      const handler = (e: KeyboardEvent) => {
        if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'p') {
          e.preventDefault()
          if (collapsed) {
            onToggleCollapse()
          }
          setQuickOpen(true)
        }
      }
      window.addEventListener('keydown', handler)
      return () => window.removeEventListener('keydown', handler)
    }, [collapsed, onToggleCollapse])

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

    // 树列拖拽调宽：分隔条是树列的左边界
    // 向右拖（delta 正）= 左边界右移 = 树列变窄
    // 向左拖（delta 负）= 左边界左移 = 树列变宽
    const handleTreeResize = useCallback((delta: number) => {
      setTreeWidth((prev) => Math.max(160, Math.min(320, prev - delta)))
    }, [])

    const activeTab = openTabs.find((t) => t.path === activePath)
    const pendingCloseName = pendingClose ? openTabs.find((t) => t.path === pendingClose)?.name : ''
    const activeDirty = activeTab ? activeTab.bufferContent !== activeTab.diskContent : false
    // .md 文件支持「代码 / 预览」切换（预览复用对话区的 markdown 渲染）
    const isMarkdown = !!activeTab && (activeTab.language === 'markdown' || activeTab.name.toLowerCase().endsWith('.md'))

    // 切换激活文件时回到源码态
    useEffect(() => {
      setPreviewMode(false)
    }, [activePath])

    // 工具面板内容：全部保持挂载（非激活用 display:none 隐藏），终端会话不丢失
    const blockCount = useChatStore((s) => s.blockIds.length)
    const tokenUsage = useChatStore((s) => s.tokenUsage)
    const toolContents: Record<ToolId, ReactNode> = {
      summary: <SummaryCard blockCount={blockCount} usage={tokenUsage} />,
      terminal: <TerminalToolContent />,
      search: <SearchPanel onFileOpen={openFile} />,
      review: <ReviewCard onFileOpen={openFile} />,
    }

    // 折叠时不渲染任何形态（入口由右缘图标轨承接）
    if (collapsed) {
      return null
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
        {/* 顶部标签栏：左文件标签 / 右工具标签分组 + 保存 + 树开关 + 折叠按钮 */}
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
          {/* 工具标签组：与文件标签同栏但分组（左侧细分隔线），默认只显示图标 */}
          <div style={{ display: 'flex', alignItems: 'stretch', borderLeft: '1px solid var(--border-subtle)', flexShrink: 0 }}>
            {TOOL_META.filter(({ id }) => toolTabsOpen.includes(id)).map(({ id, title, icon }) => {
              const active = activeToolId === id
              return (
                <div
                  key={id}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '6px',
                    padding: '0 10px',
                    cursor: 'pointer',
                    color: active ? 'var(--text-primary)' : 'var(--text-tertiary)',
                    backgroundColor: active ? 'var(--bg-secondary)' : 'transparent',
                    borderBottom: active ? '2px solid var(--accent)' : '2px solid transparent',
                    transition: 'all var(--transition-fast)',
                    position: 'relative',
                  }}
                  onClick={() => onOpenTool(id)}
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
                  title={title}
                >
                  <span style={{ display: 'flex', alignItems: 'center' }}>{icon}</span>
                  {/* 激活时显示名称，未激活靠 hover 的 title 提示 */}
                  {active && (
                    <span style={{ fontSize: '11px', fontFamily: 'var(--font-ui)', fontWeight: 500 }}>{title}</span>
                  )}
                  {/* 关闭按钮：只隐藏面板，不销毁后台状态 */}
                  <button
                    onClick={(e) => {
                      e.stopPropagation()
                      onCloseTool(id)
                    }}
                    title={`关闭${title}面板`}
                    style={{
                      border: 'none',
                      background: 'transparent',
                      color: 'var(--text-tertiary)',
                      cursor: 'pointer',
                      fontSize: '14px',
                      padding: '0',
                      lineHeight: 1,
                      width: '16px',
                      height: '16px',
                      borderRadius: '3px',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      transition: 'all var(--transition-fast)',
                    }}
                    onMouseEnter={(e) => {
                      e.currentTarget.style.background = 'var(--bg-elevated)'
                      e.currentTarget.style.color = 'var(--text-primary)'
                    }}
                    onMouseLeave={(e) => {
                      e.currentTarget.style.background = 'transparent'
                      e.currentTarget.style.color = 'var(--text-tertiary)'
                    }}
                  >
                    ×
                  </button>
                </div>
              )
            })}
          </div>
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
          {/* 文件树开关：控制右缘树窄列展开/收起 */}
          <button
            onClick={() => setTreeCollapsed((v) => !v)}
            title={treeCollapsed ? '展开文件树' : '收起文件树'}
            style={{
              border: 'none',
              background: 'transparent',
              color: treeCollapsed ? 'var(--text-tertiary)' : 'var(--accent)',
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
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.background = 'transparent'
            }}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
              <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z" />
            </svg>
          </button>
          <button
            onClick={onToggleCollapse}
            title="折叠编辑器"
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
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
              <path d="M9 6l6 6-6 6" />
            </svg>
          </button>
        </div>

        {/* 面包屑：仅文件视图显示激活文件的完整路径 */}
        {activeTab && activeToolId === null && <Breadcrumb path={activeTab.path} />}

        {/* 中部：内容区（文件/工具二选一）+ 右缘文件树窄列 */}
        <div ref={contentAreaRef} style={{ flex: 1, minHeight: 0, display: 'flex', overflow: 'hidden' }}>
          <div style={{ flex: 1, minWidth: 0, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
            {/* 文件视图：有激活文件且未激活工具标签时展示 */}
            {activeTab && activeToolId === null && (
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
                {/* .md 预览切换按钮：查看增强，默认源码编辑 */}
                {isMarkdown && (
                  <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '6px', padding: '6px 10px 0', flexShrink: 0 }}>
                    <button
                      onClick={() => setPreviewMode(false)}
                      disabled={!previewMode}
                      title="源码视图"
                      style={{
                        border: '1px solid var(--border)',
                        background: !previewMode ? 'var(--accent-soft)' : 'transparent',
                        color: !previewMode ? 'var(--accent)' : 'var(--text-secondary)',
                        cursor: previewMode ? 'pointer' : 'default',
                        padding: '3px 12px',
                        borderRadius: 'var(--radius-sm)',
                        fontSize: '11px',
                        fontFamily: 'var(--font-ui)',
                      }}
                    >
                      代码
                    </button>
                    <button
                      onClick={() => setPreviewMode(true)}
                      disabled={previewMode}
                      title="预览视图"
                      style={{
                        border: '1px solid var(--border)',
                        background: previewMode ? 'var(--accent-soft)' : 'transparent',
                        color: previewMode ? 'var(--accent)' : 'var(--text-secondary)',
                        cursor: previewMode ? 'default' : 'pointer',
                        padding: '3px 12px',
                        borderRadius: 'var(--radius-sm)',
                        fontSize: '11px',
                        fontFamily: 'var(--font-ui)',
                      }}
                    >
                      预览
                    </button>
                  </div>
                )}
                <div style={{ flex: 1, overflow: 'hidden' }}>
                  {isMarkdown && previewMode ? (
                    <div style={{ height: '100%', overflow: 'auto', padding: '4px 16px 16px', fontSize: '13px', color: 'var(--text-primary)' }}>
                      <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
                        {activeTab.bufferContent}
                      </ReactMarkdown>
                    </div>
                  ) : (
                    <CodeEditor
                      key={`${activeTab.path}:${activeTab.revision}`}
                      content={activeTab.bufferContent}
                      language={activeTab.language}
                      readOnly={!activeTab.editable}
                      onChange={(value) => handleEditorChange(activeTab.path, value)}
                    />
                  )}
                </div>
              </div>
            )}

            {/* 工具视图：激活工具标签时展示（面板与文件共用中部区域，一次只显示一个）。
                整体保持挂载、仅 CSS 隐藏，工具标签关闭后后台状态不销毁 */}
            <div
              style={{
                flex: 1,
                minHeight: 0,
                display: activeToolId !== null ? 'flex' : 'none',
                flexDirection: 'column',
                overflow: 'hidden',
              }}
            >
              {TOOL_META.map(({ id }) => (
                <div
                  key={id}
                  style={{
                    flex: 1,
                    minHeight: 0,
                    overflow: 'hidden',
                    display: activeToolId === id ? 'flex' : 'none',
                    flexDirection: 'column',
                  }}
                >
                  {toolContents[id]}
                </div>
              ))}
            </div>

            {/* 无打开文件且无激活工具标签时的空态：最近打开的文件入口 */}
            {!activeTab && activeToolId === null && (
              <div
                style={{
                  flex: 1,
                  display: 'flex',
                  flexDirection: 'column',
                  alignItems: 'center',
                  justifyContent: 'center',
                  gap: '8px',
                  color: 'var(--text-tertiary)',
                  userSelect: 'none',
                }}
              >
                <span style={{ fontSize: '13px', fontWeight: 500, color: 'var(--text-secondary)', fontFamily: 'var(--font-ui)' }}>Files</span>
                <span style={{ fontSize: '12px', fontFamily: 'var(--font-ui)' }}>没有已打开的文件</span>
                {recentFiles.length > 0 && (
                  <div
                    style={{
                      marginTop: '14px',
                      display: 'flex',
                      flexDirection: 'column',
                      alignItems: 'center',
                      gap: '4px',
                      maxWidth: '80%',
                    }}
                  >
                    <span style={{ fontSize: '11px', color: 'var(--text-tertiary)', fontFamily: 'var(--font-ui)' }}>最近打开</span>
                    {recentFiles.map((p) => (
                      <button
                        key={p}
                        onClick={() => void openFile(p)}
                        title={p}
                        style={{
                          border: 'none',
                          background: 'transparent',
                          color: 'var(--text-secondary)',
                          cursor: 'pointer',
                          fontSize: '12px',
                          fontFamily: 'var(--font-ui)',
                          padding: '2px 8px',
                          borderRadius: 'var(--radius-sm)',
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                          whiteSpace: 'nowrap',
                          maxWidth: '100%',
                          transition: 'all var(--transition-fast)',
                        }}
                        onMouseEnter={(e) => {
                          e.currentTarget.style.background = 'var(--bg-tertiary)'
                          e.currentTarget.style.color = 'var(--accent)'
                        }}
                        onMouseLeave={(e) => {
                          e.currentTarget.style.background = 'transparent'
                          e.currentTarget.style.color = 'var(--text-secondary)'
                        }}
                      >
                        {p}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>

          {/* 右缘文件树窄列：默认 220px，可拖拽 160-320px，可折叠 */}
          {!treeCollapsed && (
            <>
              <Resizer direction="horizontal" onResize={handleTreeResize} />
              <div
                style={{
                  width: treeWidth,
                  flexShrink: 0,
                  minWidth: 160,
                  display: 'flex',
                  flexDirection: 'column',
                  borderLeft: '1px solid var(--border)',
                  backgroundColor: 'var(--bg-base)',
                  overflow: 'hidden',
                }}
              >
                {/* 树列头部：标题 + 收起按钮 */}
                <div
                  style={{
                    height: '32px',
                    flexShrink: 0,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    paddingLeft: '10px',
                    borderBottom: '1px solid var(--border-subtle)',
                  }}
                >
                  <span
                    style={{
                      fontSize: '11px',
                      color: 'var(--text-tertiary)',
                      fontWeight: 500,
                      letterSpacing: '0.5px',
                      fontFamily: 'var(--font-ui)',
                      userSelect: 'none',
                    }}
                  >
                    文件
                  </span>
                  <button
                    onClick={() => setTreeCollapsed(true)}
                    title="收起文件树"
                    style={{
                      border: 'none',
                      background: 'transparent',
                      color: 'var(--text-tertiary)',
                      cursor: 'pointer',
                      padding: '0 8px',
                      height: '100%',
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
                <div style={{ flex: 1, minHeight: 0, overflow: 'hidden' }}>
                  {workspacePath ? (
                    <FileTree onFileOpen={openFile} activePath={activePath} />
                  ) : (
                    <div
                      style={{
                        padding: '16px',
                        color: 'var(--text-tertiary)',
                        fontSize: '12px',
                        fontFamily: 'var(--font-ui)',
                      }}
                    >
                      未选择工作区
                    </div>
                  )}
                </div>
              </div>
            </>
          )}
        </div>

        {/* 快速打开（Ctrl+P） */}
        <QuickOpen open={quickOpen} onClose={() => setQuickOpen(false)} onOpenFile={(p) => void openFile(p)} />

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
  }
)

EditorArea.displayName = 'EditorArea'

export default EditorArea
