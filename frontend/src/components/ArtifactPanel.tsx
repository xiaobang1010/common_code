import { forwardRef, useImperativeHandle, useState, useRef, useCallback, useEffect } from 'react'
import type { ReactNode } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import Tabs from './editor/Tabs'
import Breadcrumb from './editor/Breadcrumb'
import CodeEditor from './editor/CodeEditor'
import Terminal from './editor/Terminal'
import TabContextMenu from './editor/TabContextMenu'
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
  pinned: boolean       // 是否固定为正式标签（未固定且干净的标签参与预览槽复用）
}

// 终端会话信息
interface TerminalTab {
  id: string       // 前端分配的实例 id
  title: string    // 显示名称（pty 就绪后回填 shell 名，如 powershell）
  ptyId?: string   // 后端 pty id，创建后填充
}

// 保存冲突弹窗信息
interface ConflictInfo {
  path: string
  currentMtime: number
  currentSize: number
}

// 批量关闭待确认信息：paths 为待关闭路径，anchorPath 为右键锚点标签（激活迁移目标）
interface PendingBatchClose {
  paths: string[]
  anchorPath?: string
}

// 暴露给父组件的方法
export interface ArtifactPanelHandle {
  openFile: (path: string) => void
}

// ArtifactPanel 的 props
interface ArtifactPanelProps {
  collapsed: boolean
  onToggleCollapse: () => void
  // 工具标签状态由 App 持有（标题栏开关/快捷键共用）
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

  // pty 就绪：记录 ptyId 并把标签标题回填为实际 shell 名（如 powershell），替代固定 TERMINAL
  const handleReady = useCallback((tabId: string, ptyId: string, shell: string) => {
    setTabs((prev) =>
      prev.map((t) =>
        t.id === tabId ? { ...t, ptyId, title: shell.replace(/\.exe$/i, '') } : t
      )
    )
  }, [])

  return (
    <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}>
      {/* 会话列表栏 */}
      <div
        style={{
          height: '32px',
          display: 'flex',
          alignItems: 'stretch',
          backgroundColor: 'var(--bg-base)',
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
                backgroundColor: active ? 'var(--bg-primary)' : 'transparent',
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
            e.currentTarget.style.color = 'var(--text-primary)'
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
      <div style={{ flex: 1, overflow: 'hidden', position: 'relative' }}>
        <Terminal key={activeId} instanceId={activeId} onReady={(ptyId, shell) => handleReady(activeId, ptyId, shell)} />
        {/* 弱提示：会话未就绪/尚无输出时非纯空白，不抢焦点不打断 */}
        {!tabs.find((t) => t.id === activeId)?.ptyId && (
          <div
            style={{
              position: 'absolute',
              inset: 0,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: 'var(--text-tertiary)',
              fontSize: '12px',
              fontFamily: 'var(--font-ui)',
              pointerEvents: 'none',
            }}
          >
            暂无终端输出
          </div>
        )}
      </div>
    </div>
  )
}

const ArtifactPanel = forwardRef<ArtifactPanelHandle, ArtifactPanelProps>(
  ({ collapsed, onToggleCollapse, toolTabsOpen, activeToolId, onOpenTool, onCloseTool, onActivateFile }, ref) => {
    const [openTabs, setOpenTabs] = useState<OpenTab[]>([])
    const [activePath, setActivePath] = useState('')
    const [conflict, setConflict] = useState<ConflictInfo | null>(null)
    const [pendingClose, setPendingClose] = useState<string | null>(null)
    // 批量关闭确认弹窗（关闭全部/关闭其他/关闭右侧命中未保存文件时弹一次）
    const [pendingBatch, setPendingBatch] = useState<PendingBatchClose | null>(null)
    // 标签右键菜单：屏幕坐标 + 锚点（kind=file 为文件路径，kind=tool 为工具标签 id）
    const [tabMenu, setTabMenu] = useState<{ x: number; y: number; path: string; kind: 'file' | 'tool' } | null>(null)
    // .md 预览模式：切文件时回到源码态
    const [previewMode, setPreviewMode] = useState(false)
    // 快速打开（Ctrl+P）
    const [quickOpen, setQuickOpen] = useState(false)
    // 最近打开的文件（会话内前端内存记录，重启不持久化）
    const [recentFiles, setRecentFiles] = useState<string[]>([])

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

    // 由文件读取结果构建标签页对象（pinned 默认 false = 预览标签）
    const makeTab = (path: string, data: Awaited<ReturnType<typeof filesApi.read>>): OpenTab => ({
      path,
      name: path.split('/').pop() || path,
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
      pinned: false,
    })

    // 打开文件：已打开则切换标签，否则请求内容后新增标签。
    // 预览槽复用：当前没有固定且干净的预览标签时，整体替换该槽（path/名字/内容/基线都换掉），
    // 连续浏览不逐文件堆积；只有固定标签（pinned=true）或脏标签才新增
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
          const slot = openTabsRef.current.find((t) => !t.pinned && t.bufferContent === t.diskContent)
          if (slot) {
            updateTabs((prev) =>
              prev.map((t) => (t.path === slot.path ? { ...makeTab(path, data), revision: t.revision + 1 } : t))
            )
          } else {
            updateTabs((prev) => [...prev, makeTab(path, data)])
          }
          setActive(path)
        } catch (e) {
          console.error('读取文件失败', e)
        }
      },
      [updateTabs, setActive, collapsed, onToggleCollapse]
    )

    useImperativeHandle(ref, () => ({ openFile }), [openFile])

    // 编辑器内容变更：只更新缓冲，不回灌 value，保持 Monaco 自身 undo 栈。
    // 预览标签首次产生 dirty 时自动固定（pinned=true），此后不再参与预览槽复用
    const handleEditorChange = useCallback(
      (path: string, value: string) => {
        updateTabs((prev) =>
          prev.map((t) =>
            t.path === path && t.bufferContent !== value
              ? { ...t, bufferContent: value, pinned: t.bufferContent === t.diskContent ? true : t.pinned }
              : t
          )
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

    // 统一收尾：真正移除一批标签，处理激活迁移与自动收起判定。
    // 收起与激活迁移都以「关闭前」的工具标签激活态为准：工具面板开着时保留其激活态、不收编辑区
    const applyClose = useCallback(
      (paths: string[], anchorPath?: string) => {
        const prev = openTabsRef.current
        const closing = new Set(paths)
        const wasToolActive = activeToolIdRef.current !== null
        const next = prev.filter((t) => !closing.has(t.path))
        updateTabs(() => next)
        if (closing.has(activePathRef.current)) {
          // 激活标签被关：优先迁到锚点标签（右键所在，必在范围外），否则按关闭区间取相邻幸存标签；「关闭全部」清空
          let target = anchorPath && next.some((t) => t.path === anchorPath) ? anchorPath : ''
          if (!target) {
            const lastIdx = prev.reduce((acc, t, i) => (closing.has(t.path) ? i : acc), -1)
            const neighbor = next[lastIdx] || next[lastIdx - 1]
            target = neighbor ? neighbor.path : ''
          }
          activePathRef.current = target
          setActivePath(target)
          // 工具面板激活中不清工具激活态（面板保持可见），仅文件视图下才回清
          if (!wasToolActive) onActivateFileRef.current()
        }
        // 最后一个文件标签关掉：仅关闭前无激活工具标签时自动收起编辑区
        if (next.length === 0 && !collapsed && !wasToolActive) {
          onToggleCollapse()
        }
      },
      [updateTabs, collapsed, onToggleCollapse]
    )

    // 关闭文件标签：dirty 时先询问
    const handleClose = (path: string) => {
      const tab = openTabsRef.current.find((t) => t.path === path)
      if (tab && tab.bufferContent !== tab.diskContent) {
        setPendingClose(path)
      } else {
        applyClose([path])
      }
    }

    const handleCloseSave = async () => {
      const path = pendingClose
      if (!path) return
      const ok = await saveFile(path)
      setPendingClose(null)
      if (ok) {
        applyClose([path])
      }
    }

    const handleCloseDiscard = () => {
      const path = pendingClose
      setPendingClose(null)
      if (path) applyClose([path])
    }

    // 批量关闭入口：范围内有未保存修改时弹一次批量确认，否则直接关
    const closeTabs = useCallback(
      (paths: string[], anchorPath?: string) => {
        const hasDirty = openTabsRef.current.some(
          (t) => paths.includes(t.path) && t.bufferContent !== t.diskContent
        )
        if (hasDirty) {
          setPendingBatch({ paths, anchorPath })
        } else {
          applyClose(paths, anchorPath)
        }
      },
      [applyClose]
    )

    // 批量确认「全部保存」：按 tab 顺序逐个保存，成功即关；
    // 任一失败（含 409 冲突走既有冲突弹窗）即中止，失败与未处理标签保留、已关闭的保持关闭
    const handleBatchSave = async () => {
      const batch = pendingBatch
      if (!batch) return
      setPendingBatch(null)
      const targets = openTabsRef.current.filter((t) => batch.paths.includes(t.path))
      const closed: string[] = []
      // 干净标签（含超限只读大文件，必然无未保存修改）不走 saveFile
      // ——只读标签在 saveFile 里会被 editable 检查判为失败，不能让它中止批量流程
      for (const t of targets) {
        if (t.bufferContent === t.diskContent) closed.push(t.path)
      }
      for (const t of targets) {
        if (t.bufferContent === t.diskContent) continue
        const ok = await saveFile(t.path)
        if (!ok) break
        closed.push(t.path)
      }
      // 逐个 applyClose 会读到未刷新的旧列表、后关的把先关的复活，必须收集后一次关掉
      if (closed.length > 0) applyClose(closed, batch.anchorPath)
    }

    // 批量确认「全不保存」：放弃修改直接关闭
    const handleBatchDiscard = () => {
      const batch = pendingBatch
      setPendingBatch(null)
      if (batch) applyClose(batch.paths, batch.anchorPath)
    }

    // ---- 标签右键菜单动作 ----

    // 关闭其他：锚点标签保留，其余全关
    const closeOthers = useCallback(
      (path: string) => {
        closeTabs(openTabsRef.current.filter((t) => t.path !== path).map((t) => t.path), path)
      },
      [closeTabs]
    )

    // 关闭右侧：锚点及其左侧保留，右侧全关
    const closeRight = useCallback(
      (path: string) => {
        const idx = openTabsRef.current.findIndex((t) => t.path === path)
        closeTabs(openTabsRef.current.slice(idx + 1).map((t) => t.path), path)
      },
      [closeTabs]
    )

    // ---- 工具标签右键菜单动作（关闭 = 隐藏面板，后台状态保留，作用范围限定工具标签组）----

    const closeToolTab = useCallback((id: ToolId) => onCloseTool(id), [onCloseTool])

    const closeToolOthers = useCallback(
      (id: ToolId) => {
        for (const t of TOOL_META) {
          if (t.id !== id && toolTabsOpen.includes(t.id)) onCloseTool(t.id)
        }
      },
      [toolTabsOpen, onCloseTool]
    )

    const closeToolRight = useCallback(
      (id: ToolId) => {
        const idx = TOOL_META.findIndex((t) => t.id === id)
        for (const t of TOOL_META.slice(idx + 1)) {
          if (toolTabsOpen.includes(t.id)) onCloseTool(t.id)
        }
      },
      [toolTabsOpen, onCloseTool]
    )

    const closeToolAll = useCallback(() => {
      for (const t of TOOL_META) {
        if (toolTabsOpen.includes(t.id)) onCloseTool(t.id)
      }
    }, [toolTabsOpen, onCloseTool])

    // 打开的工具标签中按展示顺序的最后一个（「关闭右侧」置灰判定用）
    const openToolIds = TOOL_META.filter((t) => toolTabsOpen.includes(t.id))
    const lastOpenToolId = openToolIds.length > 0 ? openToolIds[openToolIds.length - 1].id : undefined

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

    // Ctrl/⌘+W 关闭当前激活文件标签；焦点在终端（xterm）内不拦截，保留删词等终端快捷键
    const handleCloseRef = useRef(handleClose)
    useEffect(() => {
      handleCloseRef.current = handleClose
    })
    useEffect(() => {
      const handler = (e: KeyboardEvent) => {
        if (!(e.ctrlKey || e.metaKey) || e.key.toLowerCase() !== 'w') return
        const target = e.target as HTMLElement | null
        if (target?.closest('.xterm')) return
        const active = openTabsRef.current.find((t) => t.path === activePathRef.current)
        if (!active) return
        e.preventDefault()
        handleCloseRef.current(active.path)
      }
      window.addEventListener('keydown', handler)
      return () => window.removeEventListener('keydown', handler)
    }, [])

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
    const activeDirty = activeTab ? activeTab.bufferContent !== activeTab.diskContent : false
    // .md 文件支持「代码 / 预览」切换（预览复用对话区的 markdown 渲染）
    const isMarkdown = !!activeTab && (activeTab.language === 'markdown' || activeTab.name.toLowerCase().endsWith('.md'))

    // 切换激活文件时回到源码态
    useEffect(() => {
      setPreviewMode(false)
    }, [activePath])

    // 文件视图节点：无工具激活（activeToolId===null）与「文件」工具（activeToolId==='files'）共用
    const fileViewNode = activeTab ? (
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
                  style={{ border: 'none', background: 'transparent', color: 'var(--text-primary)', cursor: 'pointer', fontSize: '12px', padding: 0 }}
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
                background: !previewMode ? 'var(--selected-bg)' : 'transparent',
                color: !previewMode ? 'var(--text-primary)' : 'var(--text-secondary)',
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
                background: previewMode ? 'var(--selected-bg)' : 'transparent',
                color: previewMode ? 'var(--text-primary)' : 'var(--text-secondary)',
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
    ) : null

    // 文件空态节点：无打开文件时的占位（含最近打开入口）
    const filesEmptyNode = !activeTab ? (
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
        <span style={{ fontSize: '12px', fontFamily: 'var(--font-ui)' }}>本次任务生成·修改的文件</span>
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
                  e.currentTarget.style.color = 'var(--text-primary)'
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
    ) : null

    // 工具面板内容：全部保持挂载（非激活用 display:none 隐藏），终端会话不丢失。
    // files 工具内容 = 文件视图或空态（文件视图见 fileViewNode 的展示分支）
    const blockCount = useChatStore((s) => s.blockIds.length)
    const sessionId = useChatStore((s) => s.sessionId)
    const toolContents: Record<ToolId, ReactNode> = {
      summary: <SummaryCard sessionId={sessionId} blockCount={blockCount} onOpenFile={openFile} />,
      terminal: <TerminalToolContent />,
      files: fileViewNode ?? filesEmptyNode,
      search: <SearchPanel onFileOpen={openFile} />,
      review: <ReviewCard />,
    }

    // 折叠时不渲染任何形态（入口由右上角状态胶囊卡承接）
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
              tabs={openTabs.map((t) => ({ path: t.path, name: t.name, dirty: t.bufferContent !== t.diskContent, pinned: t.pinned }))}
              activePath={activePath}
              onSwitch={setActive}
              onClose={handleClose}
              onCloseAll={() => closeTabs(openTabs.map((t) => t.path))}
              onContextMenuTab={(e, path) => {
                e.preventDefault()
                setTabMenu({ x: e.clientX, y: e.clientY, path, kind: 'file' })
              }}
            />
          )}
          {/* 工具标签组：与文件标签同栏但分组（左侧细分隔线），图标 + 名字常显。
              空间不足时本组可横向滚动；激活标签名字优先完整、未激活缩略省略 */}
          <div style={{ display: 'flex', alignItems: 'stretch', borderLeft: '1px solid var(--border-subtle)', flex: '0 1 auto', minWidth: 0, overflowX: 'auto', overflowY: 'hidden' }}>
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
                    borderBottom: active ? '2px solid var(--border-strong)' : '2px solid transparent',
                    transition: 'all var(--transition-fast)',
                    position: 'relative',
                    flexShrink: 0,
                  }}
                  onClick={() => onOpenTool(id)}
                  onContextMenu={(e) => {
                    e.preventDefault()
                    setTabMenu({ x: e.clientX, y: e.clientY, path: id, kind: 'tool' })
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
                  title={title}
                >
                  <span style={{ display: 'flex', alignItems: 'center' }}>{icon}</span>
                  {/* 名字常显：未激活浅色（继承外层容器）、激活加粗高亮；窄屏优先完整展示激活名，未激活省略 */}
                  <span
                    style={{
                      fontSize: '11px',
                      fontFamily: 'var(--font-ui)',
                      fontWeight: active ? 500 : 400,
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                      maxWidth: active ? 120 : 72,
                    }}
                  >
                    {title}
                  </span>
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
                color: activeDirty ? 'var(--text-primary)' : 'var(--text-tertiary)',
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
          {/* 顶栏不常驻文件树开关：文件树入口在左侧栏工作区行 */}
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
              e.currentTarget.style.color = 'var(--text-primary)'
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

        {/* 面包屑：文件上下文（无工具激活或文件工具内）显示激活文件的完整路径 */}
        {activeTab && (activeToolId === null || activeToolId === 'files') && <Breadcrumb path={activeTab.path} />}

        {/* 中部：内容区（文件/工具二选一）。文件树已迁至左侧栏文件树视图 */}
        <div style={{ flex: 1, minHeight: 0, display: 'flex', overflow: 'hidden' }}>
          <div style={{ flex: 1, minWidth: 0, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
            {/* 文件视图：无工具激活时展示（内容见 fileViewNode，files 工具内通过 toolContents 复用同一节点） */}
            {activeToolId === null && fileViewNode}

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

            {/* 无打开文件且无激活工具标签时的空态（内容见 filesEmptyNode，files 工具内通过 toolContents 复用） */}
            {activeToolId === null && filesEmptyNode}
          </div>
        </div>

        {/* 快速打开（Ctrl+P） */}
        <QuickOpen open={quickOpen} onClose={() => setQuickOpen(false)} onOpenFile={(p) => void openFile(p)} />

        {/* 保存冲突弹窗 */}
        {conflict && (
          <div
            style={{
              position: 'fixed',
              inset: 0,
              backgroundColor: 'var(--scrim)',
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
                  style={{ padding: '6px 12px', cursor: 'pointer', background: 'var(--button-primary-bg)', color: 'var(--button-primary-text)', border: 'none', borderRadius: 'var(--radius-sm)', fontSize: '12px' }}
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
              backgroundColor: 'var(--scrim)',
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
                  style={{ padding: '6px 12px', cursor: 'pointer', background: 'var(--button-primary-bg)', color: 'var(--button-primary-text)', border: 'none', borderRadius: 'var(--radius-sm)', fontSize: '12px' }}
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

        {/* 批量关闭确认弹窗：列出全部未保存文件，一次决策（全部保存 / 全不保存 / 取消） */}
        {pendingBatch && (
          <div
            style={{
              position: 'fixed',
              inset: 0,
              backgroundColor: 'var(--scrim)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              zIndex: 100,
            }}
            onClick={() => setPendingBatch(null)}
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
                以下 {openTabs.filter((t) => pendingBatch.paths.includes(t.path) && t.bufferContent !== t.diskContent).length} 个文件有未保存的修改：
                <br />
                <span style={{ color: 'var(--text-secondary)', fontSize: '12px' }}>
                  {openTabs
                    .filter((t) => pendingBatch.paths.includes(t.path) && t.bufferContent !== t.diskContent)
                    .map((t) => t.name)
                    .join('、')}
                </span>
                <br />
                要保存这些修改吗？
              </div>
              <div style={{ display: 'flex', gap: '10px', justifyContent: 'flex-end' }}>
                <button
                  onClick={() => void handleBatchSave()}
                  style={{ padding: '6px 12px', cursor: 'pointer', background: 'var(--button-primary-bg)', color: 'var(--button-primary-text)', border: 'none', borderRadius: 'var(--radius-sm)', fontSize: '12px' }}
                >
                  全部保存
                </button>
                <button
                  onClick={handleBatchDiscard}
                  style={{ padding: '6px 12px', cursor: 'pointer', background: 'transparent', color: 'var(--text-primary)', border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)', fontSize: '12px' }}
                >
                  全不保存
                </button>
                <button
                  onClick={() => setPendingBatch(null)}
                  style={{ padding: '6px 12px', cursor: 'pointer', background: 'transparent', color: 'var(--text-secondary)', border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)', fontSize: '12px' }}
                >
                  取消
                </button>
              </div>
            </div>
          </div>
        )}

        {/* 标签右键菜单：关闭 / 关闭其他 / 关闭右侧 / 关闭全部（文件标签关标签、工具标签隐藏面板） */}
        {tabMenu &&
          (tabMenu.kind === 'tool' ? (
            <TabContextMenu
              x={tabMenu.x}
              y={tabMenu.y}
              tabsCount={toolTabsOpen.length}
              anchorIsLast={tabMenu.path === lastOpenToolId}
              onClose={() => setTabMenu(null)}
              onCloseTab={() => closeToolTab(tabMenu.path as ToolId)}
              onCloseOthers={() => closeToolOthers(tabMenu.path as ToolId)}
              onCloseRight={() => closeToolRight(tabMenu.path as ToolId)}
              onCloseAll={closeToolAll}
            />
          ) : (
            <TabContextMenu
              x={tabMenu.x}
              y={tabMenu.y}
              tabsCount={openTabs.length}
              anchorIsLast={openTabs.length > 0 && openTabs[openTabs.length - 1].path === tabMenu.path}
              onClose={() => setTabMenu(null)}
              onCloseTab={() => handleClose(tabMenu.path)}
              onCloseOthers={() => closeOthers(tabMenu.path)}
              onCloseRight={() => closeRight(tabMenu.path)}
              onCloseAll={() => closeTabs(openTabs.map((t) => t.path))}
            />
          ))}
      </div>
    )
  }
)

ArtifactPanel.displayName = 'ArtifactPanel'

export default ArtifactPanel
