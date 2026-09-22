import { forwardRef, useImperativeHandle, useState, useRef, useCallback, useEffect } from 'react'
import type { ReactNode } from 'react'
import { subscribeFileEvents } from '../api/fileEvents'
import Tabs from './editor/Tabs'
import Breadcrumb from './editor/Breadcrumb'
import CodeEditor from './editor/CodeEditor'
import TabContextMenu from './editor/TabContextMenu'
import SearchPanel from './sidebar/SearchPanel'
import SummaryCard from './inspector/cards/SummaryCard'
import ReviewCard from './inspector/cards/ReviewCard'
import QuickOpen from './editor/QuickOpen'
import Markdown from './ai/Markdown'
import { useChatStore } from '../stores/useChatStore'
import { TOOL_META, type ToolId } from './editor/toolMeta'
import { filesApi, type FileWriteError } from '../api/client'

// 自动保存防抖：编辑停顿这么久之后落盘一次
const AUTOSAVE_DELAY_MS = 800

// 一次性保存提示（「已保存」「文件已更新」）的展示时长
const FLASH_MS = 1500

// 横幅里的文本按钮（过期重处理 / 放弃修改 / 重新加载）：与既有横幅按钮同款
const bannerActionStyle: React.CSSProperties = {
  border: 'none',
  background: 'transparent',
  color: 'var(--text-primary)',
  cursor: 'pointer',
  fontSize: '12px',
  fontFamily: 'var(--font-ui)',
  padding: 0,
  textDecoration: 'underline',
}

// 自动保存是否需要起手：有未落盘改动、可写、不在途、不处于外部改动或冲突，
// 且这次编辑还没失败过——失败后不再自动重试，等新的编辑推进序号再放行。
// 抽成纯函数便于阅读与手测复现（前端无测试基建）
function shouldAutoSave(s: {
  dirty: boolean
  editable: boolean
  saving: boolean
  stale: boolean
  conflictOpen: boolean
  editSeq: number
  failedSeq: number
}): boolean {
  return (
    s.editable && s.dirty && !s.saving && !s.stale && !s.conflictOpen && s.editSeq !== s.failedSeq
  )
}

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
  editSeq: number       // 编辑序号：每次内容改动 +1，供自动保存判断「这次编辑是否已经失败过」
  failedSeq: number     // 最近一次写盘失败对应的编辑序号（-1 表示没有失败过）
}

// 保存冲突弹窗信息（文件事件触发的冲突只有路径，没有服务端回带的当前 mtime/size）
interface ConflictInfo {
  path: string
  currentMtime?: number
  currentSize?: number
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

const ArtifactPanel = forwardRef<ArtifactPanelHandle, ArtifactPanelProps>(
  ({ collapsed, onToggleCollapse, toolTabsOpen, activeToolId, onOpenTool, onCloseTool, onActivateFile }, ref) => {
    const [openTabs, setOpenTabs] = useState<OpenTab[]>([])
    const [activePath, setActivePath] = useState('')
    const [conflict, setConflict] = useState<ConflictInfo | null>(null)
    // 标签右键菜单：屏幕坐标 + 锚点（kind=file 为文件路径，kind=tool 为工具标签 id）
    const [tabMenu, setTabMenu] = useState<{ x: number; y: number; path: string; kind: 'file' | 'tool' } | null>(null)
    // .md 预览模式：切文件时回到源码态
    const [previewMode, setPreviewMode] = useState(false)
    // 快速打开（Ctrl+P）
    const [quickOpen, setQuickOpen] = useState(false)
    // 最近打开的文件（会话内前端内存记录，重启不持久化）
    const [recentFiles, setRecentFiles] = useState<string[]>([])
    // 一次性保存提示（「已保存」「文件已更新」这类短暂文案）
    const [flash, setFlash] = useState<string>('')

    const openTabsRef = useRef<OpenTab[]>([])
    const activePathRef = useRef('')
    // 各文件在途写盘的 promise：同一文件的冲刷与保存要先等它，避免在途期间的新输入被漏写
    const savingPromisesRef = useRef<Map<string, Promise<boolean>>>(new Map())
    // 打开文件的请求序号：连续打开时只有最后一次请求的结果能落地
    const openSeqRef = useRef(0)
    const activeToolIdRef = useRef<ToolId | null>(activeToolId)
    const onActivateFileRef = useRef(onActivateFile)

    // 回调与激活态用 ref 保存，长驻监听不因重渲失效
    useEffect(() => {
      activeToolIdRef.current = activeToolId
      onActivateFileRef.current = onActivateFile
    }, [activeToolId, onActivateFile])

    const updateTabs = useCallback((updater: (prev: OpenTab[]) => OpenTab[]) => {
      setOpenTabs((prev) => {
        const next = updater(prev)
        openTabsRef.current = next
        return next
      })
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
      editSeq: 0,
      failedSeq: -1,
    })

    // 编辑器内容变更：只更新缓冲，不回灌 value，保持 Monaco 自身 undo 栈。
    // 预览标签首次产生 dirty 时自动固定（pinned=true），此后不再参与预览槽复用
    const handleEditorChange = useCallback(
      (path: string, value: string) => {
        updateTabs((prev) =>
          prev.map((t) =>
            t.path === path && t.bufferContent !== value
              ? { ...t, bufferContent: value, editSeq: t.editSeq + 1, pinned: t.bufferContent === t.diskContent ? true : t.pinned }
              : t
          )
        )
      },
      [updateTabs]
    )

    // 保存：带基线提交（乐观锁）。成功把磁盘基线推进到「本次真正写入的快照」——
    // 写盘在途期间用户继续输入时，新内容不会被误标为已保存，脏态保留、由调度补写。
    // 失败：409 走冲突弹窗（同时置 stale，自动保存据此暂停），其它错误落 error 横幅
    const saveFile = useCallback(
      async (path: string): Promise<boolean> => {
        // 同一文件的写请求串行：先等掉在途的那次
        const inflight = savingPromisesRef.current.get(path)
        if (inflight) await inflight
        const tab = openTabsRef.current.find((t) => t.path === path)
        if (!tab || !tab.editable || tab.saving) return false
        if (tab.bufferContent === tab.diskContent) return true

        const snapshot = tab.bufferContent
        const seq = tab.editSeq
        updateTabs((prev) => prev.map((t) => (t.path === path ? { ...t, saving: true, error: '' } : t)))
        const run = (async (): Promise<boolean> => {
          try {
            const result = await filesApi.write({
              path,
              content: snapshot,
              base_mtime: tab.baseMtime,
              base_size: tab.baseSize,
            })
            updateTabs((prev) =>
              prev.map((t) =>
                t.path === path
                  ? { ...t, diskContent: snapshot, baseMtime: result.mtime, baseSize: result.size, saving: false, error: '', stale: false }
                  : t
              )
            )
            setFlash('已保存')
            return true
          } catch (e) {
            const err = e as FileWriteError
            if (err.status === 409 && err.conflict) {
              // 磁盘与基线不一致：置 stale（暂停该文件的自动保存）并弹冲突窗，不落 error 横幅
              updateTabs((prev) => prev.map((t) => (t.path === path ? { ...t, saving: false, stale: true } : t)))
              setConflict({ path, currentMtime: err.conflict.current_mtime, currentSize: err.conflict.current_size })
            } else {
              // 记下这次失败的编辑序号：同一份未改动的缓冲不再自动重试
              updateTabs((prev) =>
                prev.map((t) => (t.path === path ? { ...t, saving: false, failedSeq: seq, error: err.message || '保存失败' } : t))
              )
            }
            return false
          }
        })()
        savingPromisesRef.current.set(path, run)
        try {
          return await run
        } finally {
          if (savingPromisesRef.current.get(path) === run) savingPromisesRef.current.delete(path)
        }
      },
      [updateTabs]
    )

    // 覆盖磁盘版本：不带基线强制写入（与 saveFile 共用同一在途链，保证同一文件的写请求不并发）
    const forceSave = useCallback(
      async (path: string) => {
        const inflight = savingPromisesRef.current.get(path)
        if (inflight) await inflight
        const tab = openTabsRef.current.find((t) => t.path === path)
        if (!tab) return
        const snapshot = tab.bufferContent
        const seq = tab.editSeq
        updateTabs((prev) => prev.map((t) => (t.path === path ? { ...t, saving: true, error: '' } : t)))
        const run = (async (): Promise<boolean> => {
          try {
            const result = await filesApi.write({ path, content: snapshot })
            updateTabs((prev) =>
              prev.map((t) =>
                t.path === path
                  ? { ...t, diskContent: snapshot, baseMtime: result.mtime, baseSize: result.size, saving: false, error: '', stale: false }
                  : t
              )
            )
            return true
          } catch (e) {
            const err = e as FileWriteError
            updateTabs((prev) =>
              prev.map((t) => (t.path === path ? { ...t, saving: false, failedSeq: seq, error: err.message || '保存失败' } : t))
            )
            return false
          }
        })()
        savingPromisesRef.current.set(path, run)
        try {
          await run
        } finally {
          if (savingPromisesRef.current.get(path) === run) savingPromisesRef.current.delete(path)
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

    // 静默重载：AI 改盘而该文件本地干净时自动重读。结果落地前复检缓冲与 revision——
    // 这期间用户可能已开始输入，那就放弃本次重载改走冲突出口，绝不静默覆盖
    const reloadSilently = useCallback(
      async (path: string) => {
        const before = openTabsRef.current.find((t) => t.path === path)
        if (!before) return
        const { revision, bufferContent } = before
        let data: Awaited<ReturnType<typeof filesApi.read>>
        try {
          data = await filesApi.read(path)
        } catch (e) {
          console.error('重新加载失败', e)
          return
        }
        const now = openTabsRef.current.find((t) => t.path === path)
        if (!now) return
        if (now.revision !== revision || now.bufferContent !== bufferContent) {
          // 同一文件上用户已开始输入：不覆盖，转冲突处理
          setConflict({ path })
          return
        }
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
        setFlash('文件已更新')
      },
      [updateTabs]
    )

    // 冲刷某个文件：先等在途写盘，再按最新缓冲决定是否补写。切换/关闭前必须调用；
    // 返回 false 表示没落盘，调用方要中止切换/关闭，不能丢内容
    const flushTab = useCallback(
      async (path: string): Promise<boolean> => {
        const inflight = savingPromisesRef.current.get(path)
        if (inflight) await inflight
        const tab = openTabsRef.current.find((t) => t.path === path)
        if (!tab) return true
        if (!tab.editable || tab.bufferContent === tab.diskContent) return true
        return saveFile(path)
      },
      [saveFile]
    )

    // 放弃某个文件的本次修改：不再尝试把它的缓冲写盘（文字留在编辑器里，磁盘基线保持旧值），
    // 清掉脏态/错误/过期标记让用户能继续操作；之后若再编辑触发写盘而磁盘仍不一致，
    // 会以冲突弹窗的形式再给一次选择，不会静默覆盖
    const discardLocalEdits = useCallback(
      (path: string) => {
        updateTabs((prev) =>
          prev.map((t) => (t.path === path ? { ...t, diskContent: t.bufferContent, error: '', stale: false, failedSeq: -1 } : t))
        )
      },
      [updateTabs]
    )

    // 激活文件标签：先把当前标签的待写改动落盘，再切过去（自动保存的防抖窗口不能跨标签丢内容）；
    // 冲刷失败则留在原标签，由错误横幅给出出口
    const setActive = useCallback(
      async (path: string) => {
        const prev = activePathRef.current
        if (prev && prev !== path && !(await flushTab(prev))) return
        activePathRef.current = path
        setActivePath(path)
        onActivateFileRef.current()
      },
      [flushTab]
    )

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
          // 已打开：只切激活。同时推进请求序号，让仍在途的其它打开请求作废
          ++openSeqRef.current
          void setActive(path)
          return
        }
        // 请求序号：连续打开两个文件时，先发出的 read 若后返回不能覆盖后点的结果
        const seq = ++openSeqRef.current
        try {
          const data = await filesApi.read(path)
          if (seq !== openSeqRef.current) return
          const slot = openTabsRef.current.find((t) => !t.pinned && t.bufferContent === t.diskContent)
          if (slot) {
            updateTabs((prev) =>
              prev.map((t) => (t.path === slot.path ? { ...makeTab(path, data), revision: t.revision + 1 } : t))
            )
          } else {
            updateTabs((prev) => [...prev, makeTab(path, data)])
          }
          void setActive(path)
        } catch (e) {
          console.error('读取文件失败', e)
          if (seq === openSeqRef.current) setFlash('打开文件失败')
        }
      },
      [updateTabs, setActive, collapsed, onToggleCollapse]
    )

    useImperativeHandle(ref, () => ({ openFile }), [openFile])

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
        if (next.length === 0) {
          setFlash('')
          if (!collapsed && !wasToolActive) onToggleCollapse()
        }
      },
      [updateTabs, collapsed, onToggleCollapse]
    )

    // 关闭文件标签：先把待写改动落盘，成功才关（自动保存下不再需要「保存/不保存」询问）；
    // 冲刷失败则保留该标签与内容，错误横幅给出口
    const handleClose = async (path: string) => {
      if (!(await flushTab(path))) return
      applyClose([path])
    }

    // 批量关闭入口：逐个冲刷（有未落盘改动的先落盘），成功一个关一个；
    // 任一个冲刷失败即中止，失败的与尚未处理的标签都保留（不再弹批量确认）
    const closeTabs = useCallback(
      async (paths: string[], anchorPath?: string) => {
        const closed: string[] = []
        for (const p of paths) {
          if (!(await flushTab(p))) break
          closed.push(p)
        }
        if (closed.length > 0) applyClose(closed, anchorPath)
      },
      [flushTab, applyClose]
    )

    // ---- 标签右键菜单动作 ----

    // 关闭其他：锚点标签保留，其余全关
    const closeOthers = useCallback(
      (path: string) => {
        void closeTabs(openTabsRef.current.filter((t) => t.path !== path).map((t) => t.path), path)
      },
      [closeTabs]
    )

    // 关闭右侧：锚点及其左侧保留，右侧全关
    const closeRight = useCallback(
      (path: string) => {
        const idx = openTabsRef.current.findIndex((t) => t.path === path)
        void closeTabs(openTabsRef.current.slice(idx + 1).map((t) => t.path), path)
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

    // 存在未落盘状态时（脏缓冲 / 写盘失败 / 正在写 / 冲突未决），刷新或关窗给出确认提示。
    // 自动保存已把常规编辑的丢失窗口压到防抖时长内，这里只做保底提示：卸载阶段的异步写不可靠，不做 flush
    useEffect(() => {
      const handler = (e: BeforeUnloadEvent) => {
        const pending = openTabsRef.current.some(
          (t) => t.bufferContent !== t.diskContent || !!t.error || t.saving
        )
        if (pending || conflict) {
          e.preventDefault()
          e.returnValue = ''
        }
      }
      window.addEventListener('beforeunload', handler)
      return () => window.removeEventListener('beforeunload', handler)
    }, [conflict])

    // Ctrl+S 立即冲刷当前标签（拦截浏览器默认保存行为）。自动保存已开启，这里作手动兜底：
    // 取消防抖立刻写盘；写失败后也能靠它重试（不经失败闸门）
    useEffect(() => {
      const handler = (e: KeyboardEvent) => {
        if (e.ctrlKey && e.key === 's') {
          e.preventDefault()
          if (activePathRef.current) void flushTab(activePathRef.current)
        }
      }
      window.addEventListener('keydown', handler)
      return () => window.removeEventListener('keydown', handler)
    }, [flushTab])

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

    // 订阅文件变更事件：AI 改盘后按该文件本地是否干净分流——干净就静默重载（内容与磁盘一致，
    // 重载无损失），有本地修改则置 stale 并弹冲突窗（不静默覆盖）。逐条精确匹配 path，不做防抖
    useEffect(
      () =>
        subscribeFileEvents((evt) => {
          if (evt.type !== 'file_changed' || !evt.path) return
          const path = evt.path
          const tab = openTabsRef.current.find((t) => t.path === path)
          if (!tab) return
          updateTabs((prev) => prev.map((t) => (t.path === path ? { ...t, stale: true } : t)))
          if (tab.bufferContent === tab.diskContent) {
            void reloadSilently(path)
          } else {
            setConflict({ path })
          }
        }),
      [updateTabs, reloadSilently],
    )

    const activeTab = openTabs.find((t) => t.path === activePath)
    // .md 文件支持「代码 / 预览」切换（预览复用对话区的 markdown 渲染）
    const isMarkdown = !!activeTab && (activeTab.language === 'markdown' || activeTab.name.toLowerCase().endsWith('.md'))

    // 切换激活文件时回到源码态
    useEffect(() => {
      setPreviewMode(false)
    }, [activePath])

    // 一次性提示（「已保存」「文件已更新」）短暂展示后自动隐去
    useEffect(() => {
      if (!flash) return
      const timer = setTimeout(() => setFlash(''), FLASH_MS)
      return () => clearTimeout(timer)
    }, [flash])

    // 编辑停顿后自动落盘当前标签。依赖覆盖内容、在途状态、外部改动、冲突与编辑序号：
    // 带 saving/diskContent 是为了「写盘结束后若仍 dirty（在途期间又有新输入）能重新起手」，
    // 带 editSeq 是为了「新编辑解除失败闸门」——失败闸门保证持续失败不会被周期性重试
    useEffect(() => {
      if (!activeTab) return
      const ok = shouldAutoSave({
        dirty: activeTab.bufferContent !== activeTab.diskContent,
        editable: activeTab.editable,
        saving: activeTab.saving,
        stale: activeTab.stale,
        conflictOpen: !!conflict,
        editSeq: activeTab.editSeq,
        failedSeq: activeTab.failedSeq,
      })
      if (!ok) return
      const path = activeTab.path
      const timer = setTimeout(() => {
        void saveFile(path)
      }, AUTOSAVE_DELAY_MS)
      return () => clearTimeout(timer)
    }, [activeTab, conflict, saveFile])

    // 冲刷点：窗口失焦与页面隐藏时把所有待写改动落盘，不留在防抖窗口里
    useEffect(() => {
      const flushAll = () => {
        for (const t of openTabsRef.current) {
          if (t.editable && t.bufferContent !== t.diskContent) void flushTab(t.path)
        }
      }
      const onBlur = () => flushAll()
      const onVisibility = () => {
        if (document.visibilityState === 'hidden') flushAll()
      }
      window.addEventListener('blur', onBlur)
      document.addEventListener('visibilitychange', onVisibility)
      return () => {
        window.removeEventListener('blur', onBlur)
        document.removeEventListener('visibilitychange', onVisibility)
      }
    }, [flushTab])

    // 面板收起：先把所有待写改动落盘再隐藏
    useEffect(() => {
      if (!collapsed) return
      for (const t of openTabsRef.current) {
        if (t.editable && t.bufferContent !== t.diskContent) void flushTab(t.path)
      }
    }, [collapsed, flushTab])

    // 顶栏保存状态：当前标签写盘在途 / 写失败（红）/ 一次性提示；空闲时不占位
    const saveStatus = activeTab?.saving
      ? { text: '保存中…', color: 'var(--text-tertiary)' }
      : activeTab?.error
        ? { text: '保存失败', color: 'var(--error)' }
        : flash
          ? { text: flash, color: 'var(--text-tertiary)' }
          : null

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
              <>
                <span>磁盘已变更，你的修改尚未写入。</span>
                <button onClick={() => setConflict({ path: activeTab.path })} style={bannerActionStyle}>
                  重新处理
                </button>
                <button
                  onClick={() => discardLocalEdits(activeTab.path)}
                  title="放弃保存当前修改（内容不写入磁盘）"
                  style={bannerActionStyle}
                >
                  放弃本次修改并继续
                </button>
              </>
            ) : (
              <>
                <span>磁盘已变更，正在重新加载…</span>
                {/* 自动重载失败（例如文件被删）时的兜底入口 */}
                <button onClick={() => void reloadTab(activeTab.path)} style={bannerActionStyle}>
                  重新加载
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
              display: 'flex',
              alignItems: 'center',
              gap: '10px',
            }}
          >
            <span>{activeTab.error}</span>
            {/* 写盘失败且有未落盘内容时给一条出路：放弃保存，回到可继续操作的状态 */}
            {activeTab.bufferContent !== activeTab.diskContent && (
              <button
                onClick={() => discardLocalEdits(activeTab.path)}
                title="放弃保存当前修改（内容不写入磁盘）"
                style={bannerActionStyle}
              >
                放弃本次修改并继续
              </button>
            )}
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
              <Markdown content={activeTab.bufferContent} />
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

    // 工具面板内容：全部保持挂载（非激活用 display:none 隐藏）。
    // 「文件」不再是工具标签：面板无工具激活时的基础视图就是文件视图
    // 终端已迁至会话区底部独立面板，不再是右侧工具标签
    const sessionId = useChatStore((s) => s.sessionId)
    const toolContents: Record<ToolId, ReactNode> = {
      summary: <SummaryCard sessionId={sessionId} onOpenFile={openFile} />,
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
          {/* 保存状态：脏缓冲由自动保存在停顿后落盘，这里只给轻量反馈，空闲时不占位 */}
          {saveStatus && (
            <span
              title="已开启自动保存，Ctrl+S 可立即保存"
              style={{
                display: 'flex',
                alignItems: 'center',
                padding: '0 10px',
                fontSize: '11px',
                fontFamily: 'var(--font-ui)',
                color: saveStatus.color,
                whiteSpace: 'nowrap',
                flexShrink: 0,
              }}
            >
              {saveStatus.text}
            </span>
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
        {activeTab && activeToolId === null && <Breadcrumb path={activeTab.path} />}

        {/* 中部：内容区（文件/工具二选一）。文件树已迁至左侧栏文件树视图 */}
        <div style={{ flex: 1, minHeight: 0, display: 'flex', overflow: 'hidden' }}>
          <div style={{ flex: 1, minWidth: 0, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
            {/* 文件视图：面板的基础视图（无工具激活时展示，内容见 fileViewNode） */}
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

            {/* 无打开文件且无工具激活时的空态（内容见 filesEmptyNode） */}
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
