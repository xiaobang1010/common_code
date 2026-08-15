import { useState, useRef, useCallback, useEffect, useMemo } from 'react'
import Sidebar from './components/Sidebar'
import EditorArea, { type EditorAreaHandle } from './components/EditorArea'
import AIPanel from './components/AIPanel'
import TitleBar from './components/TitleBar'
import IconRail from './components/IconRail'
import Resizer from './components/Resizer'
import SettingsModal from './components/settings/SettingsModal'
import WorkspaceSelector from './components/ai/WorkspaceSelector'
import BranchSelector from './components/ai/BranchSelector'
import { useChatStore } from './stores/useChatStore'
import { useSettingsStore } from './stores/useSettingsStore'
import { useSessions } from './hooks/useSessions'
import { TOOL_META, type ToolId } from './components/editor/toolMeta'
import { gitApi, sessionsApi } from './api/client'

// 布局宽度预算：对话区是主角，有最小宽度保护；编辑区宽度设上下限
const SIDEBAR_MIN = 180
const SIDEBAR_MAX = 500
const CHAT_MIN_WIDTH = 360 // 对话区最小宽度：任何情况下不被挤到不可读
const EDITOR_MIN = 360
const EDITOR_MAX_RATIO = 0.85 // 编辑器最多占窗口 85%

// 布局持久化 key：宽度与面板开关状态重启后恢复，设置面板提供「恢复默认布局」
const LAYOUT_KEYS = {
  sidebarWidth: 'layout.sidebarWidth',
  editorWidth: 'layout.editorWidth',
  treeWidth: 'layout.treeWidth',
  treeCollapsed: 'layout.treeCollapsed',
  toolTabsOpen: 'layout.toolTabsOpen',
  activeToolId: 'layout.activeToolId',
} as const

function App() {
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
  // 编辑器按需显示：默认隐藏，打开文件时才出现
  const [editorCollapsed, setEditorCollapsed] = useState(true)

  // 工具标签（概要/终端/搜索/审查）开关状态：由 App 持有，标题栏开关/图标轨/快捷键共用
  const [toolTabsOpen, setToolTabsOpen] = useState<ToolId[]>(() => {
    try {
      const v = JSON.parse(localStorage.getItem(LAYOUT_KEYS.toolTabsOpen) || '[]')
      return Array.isArray(v) ? v.filter((x): x is ToolId => TOOL_META.some((t) => t.id === x)) : []
    } catch {
      return []
    }
  })
  const [activeToolId, setActiveToolId] = useState<ToolId | null>(() => {
    const v = localStorage.getItem(LAYOUT_KEYS.activeToolId)
    return v && TOOL_META.some((t) => t.id === v) ? (v as ToolId) : null
  })
  // 最近使用的工具标签：标题栏开关展开编辑区时聚焦它
  const lastToolIdRef = useRef<ToolId | null>(activeToolId)

  // 设置 Modal 开关
  const [settingsOpen, setSettingsOpen] = useState(false)

  // 侧边栏和编辑器的像素宽度（折叠时不起作用，恢复时用得上），持久化恢复
  const [sidebarWidth, setSidebarWidth] = useState(() => {
    const v = Number(localStorage.getItem(LAYOUT_KEYS.sidebarWidth))
    return v >= SIDEBAR_MIN && v <= SIDEBAR_MAX ? v : 240
  })
  const [editorWidth, setEditorWidth] = useState(() => {
    // 0 表示用 flex 比例；恢复时按当前窗口 85% 上限钳制
    const v = Number(localStorage.getItem(LAYOUT_KEYS.editorWidth))
    return v > 0 ? Math.min(v, window.innerWidth * EDITOR_MAX_RATIO) : 0
  })

  // 聊天状态从 store 订阅：action 引用稳定，不会因流式更新引起本组件重渲
  const chatSessionId = useChatStore(s => s.sessionId)
  const setSessionId = useChatStore(s => s.setSessionId)
  const disconnectStream = useChatStore(s => s.disconnectStream)
  const loadMessages = useChatStore(s => s.loadMessages)
  const clearMessages = useChatStore(s => s.clearMessages)
  const fetchState = useChatStore(s => s.fetchState)
  const isStreaming = useChatStore(s => s.isStreaming)
  const sessions = useSessions()
  const editorRef = useRef<EditorAreaHandle>(null)

  // 当前任务标题（标题栏展示）：从分组数据找当前会话，侧栏折叠时仍可见
  const currentTaskTitle = useMemo(() => {
    for (const g of sessions.allGroups) {
      const found = g.sessions.find(s => s.id === chatSessionId)
      if (found) return found.title || '新任务'
    }
    return ''
  }, [sessions.allGroups, chatSessionId])

  // 任务运行态刷新：流式开始/结束时重新拉取分组列表。
  // grouped 的 current_task 是快照，不发消息就没有刷新时机，必须由
  // isStreaming 变化驱动，运行指示才能出现与消失
  useEffect(() => {
    sessions.loadAllSessions()
  }, [isStreaming]) // eslint-disable-line react-hooks/exhaustive-deps

  // 当前 Git 分支和分支列表
  const [currentBranch, setCurrentBranch] = useState('')
  const [branches, setBranches] = useState<string[]>([])

  // 标记初始会话是否已加载，避免重复加载
  const initialSessionLoaded = useRef(false)

  // 工作区变化时加载分支列表
  useEffect(() => {
    if (!sessions.currentWorkspace) return
    gitApi.branches(sessions.currentWorkspace.path)
      .then(data => {
        setBranches(data.branches)
        setCurrentBranch(data.current)
      })
      .catch(() => {
        setBranches([])
        setCurrentBranch('')
      })
  }, [sessions.currentWorkspace?.path]) // eslint-disable-line react-hooks/exhaustive-deps

  // 初始拉取后端汇总状态；改 LLM 配置后刷新 model 显示（原 useChat 内部逻辑）
  useEffect(() => {
    fetchState()
  }, [fetchState])
  const modelVersion = useSettingsStore((s) => s.modelVersion)
  useEffect(() => {
    if (modelVersion > 0) fetchState()
  }, [modelVersion, fetchState])

  // 初始会话加载：第一次有会话时加载消息
  useEffect(() => {
    if (initialSessionLoaded.current) return
    if (!sessions.currentSessionId) return
    initialSessionLoaded.current = true
    const id = sessions.currentSessionId
    sessions.switchSession(id).then(messages => {
      setSessionId(id)
      if (messages) loadMessages(messages)
    }).catch(e => {
      // 初始加载失败：提示用户，本地状态不动（引擎未覆盖，不会串数据）
      alert(`加载会话失败：${e instanceof Error ? e.message : '未知错误'}`)
    })
  }, [sessions.currentSessionId]) // eslint-disable-line react-hooks/exhaustive-deps

  // ---- 布局持久化 ----
  useEffect(() => {
    localStorage.setItem(LAYOUT_KEYS.sidebarWidth, String(sidebarWidth))
  }, [sidebarWidth])
  useEffect(() => {
    localStorage.setItem(LAYOUT_KEYS.editorWidth, String(editorWidth))
  }, [editorWidth])
  useEffect(() => {
    localStorage.setItem(LAYOUT_KEYS.toolTabsOpen, JSON.stringify(toolTabsOpen))
  }, [toolTabsOpen])
  useEffect(() => {
    localStorage.setItem(LAYOUT_KEYS.activeToolId, activeToolId ?? '')
  }, [activeToolId])

  // 恢复默认布局：清持久化并重置各宽度与面板开关（树状态由 EditorArea 监听事件重置）
  const resetLayout = useCallback(() => {
    Object.values(LAYOUT_KEYS).forEach((k) => localStorage.removeItem(k))
    setSidebarWidth(240)
    setEditorWidth(0)
    setToolTabsOpen([])
    setActiveToolId(null)
    setSidebarCollapsed(false)
    setEditorCollapsed(true)
    window.dispatchEvent(new Event('layout-reset'))
  }, [])

  // 窄屏退让：树列折叠由 EditorArea 按编辑区/窗口宽度处理；
  // 这里兜底：窗口仍不足以容纳「会话栏 + 对话区 360 + 编辑区 360」时会话栏自动折叠为窄条
  useEffect(() => {
    const onResize = () => {
      if (window.innerWidth < SIDEBAR_MIN + CHAT_MIN_WIDTH + EDITOR_MIN) {
        setSidebarCollapsed(true)
      }
    }
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  // 侧边栏拖拽：向右拖增加宽度，向左拖减少
  const handleSidebarResize = useCallback((delta: number) => {
    setSidebarWidth((prev) => {
      const next = Math.max(SIDEBAR_MIN, Math.min(SIDEBAR_MAX, prev + delta))
      return next
    })
  }, [])

  // 编辑器拖拽：分隔条是编辑器的左边界
  // 向右拖分隔条（delta 正）= 左边界右移 = 编辑器变窄
  // 向左拖分隔条（delta 负）= 左边界左移 = 编辑器变宽
  // 分隔条跟着鼠标走，符合物理直觉
  const handleEditorResize = useCallback((delta: number) => {
    setEditorWidth((prev) => {
      const maxW = window.innerWidth * EDITOR_MAX_RATIO
      return Math.max(EDITOR_MIN, Math.min(maxW, prev - delta))
    })
  }, [])

  // 折叠/展开侧边栏
  const toggleSidebar = useCallback(() => {
    setSidebarCollapsed((prev) => !prev)
  }, [])

  // 折叠/展开编辑器
  const toggleEditor = useCallback(() => {
    setEditorCollapsed((prev) => {
      // 第一次展开时如果没设过宽度，给个默认值（窗口的 40%，钳制在上下限内）
      if (!prev && editorWidth === 0) {
        const maxW = window.innerWidth * EDITOR_MAX_RATIO
        setEditorWidth(Math.max(EDITOR_MIN, Math.min(maxW, Math.floor(window.innerWidth * 0.4))))
      }
      return !prev
    })
  }, [editorWidth])

  // ---- 工具标签操作 ----

  // 打开工具标签：展开编辑区并激活该面板
  const openTool = useCallback(
    (id: ToolId) => {
      if (editorCollapsed) {
        toggleEditor()
      }
      lastToolIdRef.current = id
      setToolTabsOpen((prev) => (prev.includes(id) ? prev : [...prev, id]))
      setActiveToolId(id)
    },
    [editorCollapsed, toggleEditor]
  )

  // 关闭工具标签：只隐藏面板，后台状态保留（终端会话不杀、审查结果不清除）
  const closeTool = useCallback((id: ToolId) => {
    setToolTabsOpen((prev) => prev.filter((t) => t !== id))
    setActiveToolId((prev) => (prev === id ? null : prev))
  }, [])

  // 文件标签被激活：工具标签取消激活（内容区回到文件视图）
  const activateFile = useCallback(() => {
    setActiveToolId(null)
  }, [])

  // 图标轨点击：已激活则收回，否则直达对应面板
  const handleRailToolClick = useCallback(
    (id: ToolId) => {
      if (activeToolId === id && !editorCollapsed) {
        closeTool(id)
      } else {
        openTool(id)
      }
    },
    [activeToolId, editorCollapsed, closeTool, openTool]
  )

  // 标题栏面板开关：展开编辑区并聚焦最近工具标签；已展开则收起
  const togglePanel = useCallback(() => {
    if (!editorCollapsed) {
      toggleEditor()
      return
    }
    const last = lastToolIdRef.current ?? 'summary'
    toggleEditor()
    setToolTabsOpen((prev) => (prev.includes(last) ? prev : [...prev, last]))
    setActiveToolId(last)
  }, [editorCollapsed, toggleEditor])

  // ---- 会话管理相关回调 ----

  // 删除类操作确认：删除会话/工作区会中止任务并删记录，统一提示
  const confirmIfStreaming = useCallback((): boolean => {
    if (!isStreaming) return true
    return window.confirm('任务进行中，该操作将停止当前任务，确定继续？')
  }, [isStreaming])

  // 切回运行中会话：轻轮询 /api/state 拉实时消息展示任务进展；
  // 后台任务结束后拉取最终消息
  const prevTaskRunningRef = useRef(false)
  const lastMsgCountRef = useRef(0)
  useEffect(() => {
    const taskRunning = sessions.currentTasks.some(t => t.session_id === chatSessionId)
    if (!taskRunning) {
      // 任务刚结束（之前在跑 -> 现在不在）：刷新消息列表展示后台任务产出
      if (prevTaskRunningRef.current) {
        prevTaskRunningRef.current = false
        lastMsgCountRef.current = 0
        if (chatSessionId) {
          sessionsApi.get(chatSessionId)
            .then(detail => loadMessages(detail.messages))
            .catch(() => {})
        }
      }
      return
    }
    prevTaskRunningRef.current = true
    const timer = setInterval(async () => {
      // 刷新运行态标记
      sessions.loadAllSessions()
      // 拉取任务引擎实时消息（消息数变化才重建视图，避免闪烁）
      try {
        const resp = await fetch('/api/state')
        const data = await resp.json()
        if (Array.isArray(data.messages) && data.messages.length !== lastMsgCountRef.current) {
          lastMsgCountRef.current = data.messages.length
          loadMessages(data.messages)
        }
      } catch {
        // 忽略瞬时失败
      }
    }, 2000)
    return () => clearInterval(timer)
  }, [chatSessionId, sessions.currentTasks]) // eslint-disable-line react-hooks/exhaustive-deps

  // 当前查看会话是否在跑后台任务（运行态标记与输入提示用）
  const runningSessionId = sessions.currentTasks.some(t => t.session_id === chatSessionId)
    ? chatSessionId
    : null

  // 后端自动建会话（session_meta）回传后：同步会话 hook 的选中态并刷新列表。
  // 幂等条件：chatSessionId 与 hook 源一致时不动作（初始加载/手动切换走各自逻辑）
  useEffect(() => {
    if (!chatSessionId) return
    if (sessions.currentSessionId !== chatSessionId) {
      sessions.updateCurrentSessionId(chatSessionId)
      sessions.loadSessions()
      sessions.loadAllSessions()
    }
  }, [chatSessionId, sessions]) // eslint-disable-line react-hooks/exhaustive-deps

  // 新建会话：创建后设为当前，清空消息（任务后台继续，无需确认）
  const handleCreateSession = useCallback(async () => {
    disconnectStream()
    const id = await sessions.createSession()
    if (id) {
      setSessionId(id)
      clearMessages()
    }
  }, [sessions, setSessionId, clearMessages])

  // 切换会话：加载消息到聊天面板（不中止后台任务，任务继续写回原会话）。
  // 切换失败时（404/网络错误）：不改本地状态、不清空界面，提示用户——
  // 否则界面显示已切换而后端引擎仍是旧会话，下次发消息会把旧会话历史
  // 写进目标会话（前后端脱钩串数据）
  const handleSwitchSession = useCallback(async (sessionId: string) => {
    // 断开当前 SSE 连接：任务在后台继续跑，本地恢复可发送状态
    disconnectStream()
    try {
      const messages = await sessions.switchSession(sessionId)
      setSessionId(sessionId)
      if (messages) {
        loadMessages(messages)
      } else {
        clearMessages()
      }
    } catch (e) {
      alert(`切换会话失败：${e instanceof Error ? e.message : '未知错误'}`)
    }
  }, [sessions, setSessionId, loadMessages, clearMessages])

  // 删除会话：如果删的是当前会话，加载新的当前会话。
  // 删除后的内嵌切换失败时：会话已删、本地 id 必须更新，后端已重置引擎，
  // 清空界面并提示（引擎为空不会串数据）
  const handleDeleteSession = useCallback(async (sessionId: string) => {
    if (!confirmIfStreaming()) return
    disconnectStream()
    const wasCurrent = sessionId === chatSessionId
    const newSessionId = await sessions.deleteSession(sessionId)
    if (wasCurrent) {
      setSessionId(newSessionId)
      if (newSessionId) {
        // 加载新当前会话的消息
        try {
          const messages = await sessions.switchSession(newSessionId)
          if (messages) loadMessages(messages)
          else clearMessages()
        } catch (e) {
          clearMessages()
          alert(`切换会话失败：${e instanceof Error ? e.message : '未知错误'}`)
        }
      } else {
        clearMessages()
      }
    }
  }, [sessions, setSessionId, loadMessages, clearMessages, chatSessionId, disconnectStream])

  // 跨工作区切换会话：可能需要先切换工作区（不中止后台任务）。
  // 失败时不改本地状态、不清空界面并提示（同 handleSwitchSession）
  const handleSwitchInWorkspace = useCallback(async (sessionId: string, workspacePath: string) => {
    disconnectStream()
    try {
      const result = await sessions.switchToSessionInWorkspace(sessionId, workspacePath)
      if (result) {
        setSessionId(sessionId)
        if (result.messages) {
          loadMessages(result.messages)
        } else {
          clearMessages()
        }
        // 更新分支信息
        if (result.branch !== undefined) {
          setCurrentBranch(result.branch)
        }
        // 刷新分支列表（工作区可能变了）
        if (sessions.currentWorkspace) {
          gitApi.branches(sessions.currentWorkspace.path)
            .then(data => {
              setBranches(data.branches)
              setCurrentBranch(data.current)
            })
            .catch(() => {})
        }
      }
    } catch (e) {
      alert(`切换会话失败：${e instanceof Error ? e.message : '未知错误'}`)
    }
  }, [sessions, setSessionId, loadMessages, clearMessages])

  // 切换工作区：更新分支，加载新当前会话（不中止后台任务）。
  // 嵌套切换失败时提示用户，本地状态不动（后端引擎未覆盖，不会串数据）
  const handleSwitchWorkspace = useCallback(async (path: string) => {
    disconnectStream()
    const result = await sessions.switchWorkspace(path)
    if (result) {
      setCurrentBranch(result.branch)
      // 加载新当前会话的消息
      if (result.sessionId) {
        try {
          const messages = await sessions.switchSession(result.sessionId)
          setSessionId(result.sessionId)
          if (messages) loadMessages(messages)
        } catch (e) {
          alert(`切换会话失败：${e instanceof Error ? e.message : '未知错误'}`)
        }
      } else {
        setSessionId(null)
        clearMessages()
      }
    }
  }, [sessions, setSessionId, loadMessages, clearMessages])

  // 浏览选择目录
  const handleBrowse = useCallback(async () => {
    const w = window as unknown as { electronAPI?: { selectDirectory?: () => Promise<string | null> } }
    const path = await w.electronAPI?.selectDirectory?.()
    if (path) {
      handleSwitchWorkspace(path)
    }
  }, [handleSwitchWorkspace])

  // 切换 Git 分支
  const handleCheckout = useCallback(async (branch: string) => {
    try {
      await gitApi.checkout(branch)
      setCurrentBranch(branch)
    } catch {
      // 切换失败静默忽略
    }
  }, [])

  // 移除工作区：删除后刷新列表，如果删的是当前工作区则清空聊天状态。
  // 删除工作区会连名下所有会话一起删除，运行中的任务也会被中止，需确认
  const handleRemoveWorkspace = useCallback(async (workspacePath: string) => {
    if (!confirmIfStreaming()) return
    if (!window.confirm('删除工作区将删除其名下所有会话，确定继续？')) return
    const isCurrent = workspacePath === sessions.currentWorkspace?.path
    await sessions.deleteWorkspace(workspacePath)
    if (isCurrent) {
      // 删的是当前工作区，清空聊天和会话状态
      setSessionId(null)
      clearMessages()
      setCurrentBranch('')
      setBranches([])
    }
  }, [sessions, setSessionId, clearMessages])

  // 打开工作区：弹出目录选择对话框
  const handleOpenWorkspace = useCallback(async () => {
    const w = window as unknown as { electronAPI?: { selectDirectory?: () => Promise<string | null> } }
    const path = await w.electronAPI?.selectDirectory?.()
    if (path) {
      handleSwitchWorkspace(path)
    }
  }, [handleSwitchWorkspace])

  // 全局快捷键：Ctrl/⌘+N 新建任务，Ctrl/⌘+K 打开搜索工具标签
  // Electron 中这两组快捷键无默认系统行为，全局拦截安全
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      const mod = e.ctrlKey || e.metaKey
      if (!mod) return
      if (e.key.toLowerCase() === 'n') {
        e.preventDefault()
        handleCreateSession()
      } else if (e.key.toLowerCase() === 'k') {
        e.preventDefault()
        openTool('search')
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [handleCreateSession, openTool])

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        height: '100vh',
        width: '100vw',
        overflow: 'hidden',
      }}
    >
      {/* 自绘标题栏：拖拽窗口 + 工作区/分支选择 + 业务图标 + 溢出菜单（原 AIPanel 工具条上提合并） */}
      <TitleBar
        workspaceSelector={
          <WorkspaceSelector
            currentWorkspace={sessions.currentWorkspace}
            workspaces={sessions.workspaces}
            onSwitch={handleSwitchWorkspace}
            onBrowse={handleBrowse}
          />
        }
        branchSelector={
          <BranchSelector
            currentBranch={currentBranch}
            branches={branches}
            onCheckout={handleCheckout}
          />
        }
        panelActive={!editorCollapsed}
        onTogglePanel={togglePanel}
        onOpenSettings={() => setSettingsOpen(true)}
        onNewSession={handleCreateSession}
        currentTaskTitle={currentTaskTitle}
      />

      {/* 主体行：会话栏 + AI面板 + 编辑区 + 右缘图标轨 */}
      <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
        {/* 会话栏：折叠时不占位，展开时固定宽度 + 可拖拽 */}
        {!sidebarCollapsed && (
          <>
            <div style={{ width: sidebarWidth, flexShrink: 0 }}>
              <Sidebar
                collapsed={false}
                onToggleCollapse={toggleSidebar}
                groups={sessions.allGroups}
                currentWorkspacePath={sessions.currentWorkspace?.path ?? null}
                currentSessionId={sessions.currentSessionId}
                onCreateSession={handleCreateSession}
                onSwitchSession={handleSwitchSession}
                onSwitchInWorkspace={handleSwitchInWorkspace}
                onDeleteSession={handleDeleteSession}
                onRemoveWorkspace={handleRemoveWorkspace}
                onOpenWorkspace={handleOpenWorkspace}
                onOpenSearch={() => openTool('search')}
                runningSessionId={runningSessionId}
                onRenameSession={sessions.renameSession}
                onToggleSessionPin={sessions.toggleSessionPin}
                onUpdateWorkspace={sessions.updateWorkspaceMeta}
              />
            </div>
            <Resizer direction="horizontal" onResize={handleSidebarResize} />
          </>
        )}
        {sidebarCollapsed && (
          <Sidebar
            collapsed={true}
            onToggleCollapse={toggleSidebar}
            groups={sessions.allGroups}
            currentWorkspacePath={sessions.currentWorkspace?.path ?? null}
            currentSessionId={sessions.currentSessionId}
            onCreateSession={handleCreateSession}
            onSwitchSession={handleSwitchSession}
            onSwitchInWorkspace={handleSwitchInWorkspace}
            onDeleteSession={handleDeleteSession}
            onRemoveWorkspace={handleRemoveWorkspace}
            onOpenWorkspace={handleOpenWorkspace}
            onOpenSearch={() => openTool('search')}
            runningSessionId={runningSessionId}
            onRenameSession={sessions.renameSession}
            onToggleSessionPin={sessions.toggleSessionPin}
            onUpdateWorkspace={sessions.updateWorkspaceMeta}
          />
        )}

        {/* AI 面板：占据剩余空间（主角），最小宽度受保护不被挤没 */}
        <div style={{ flex: 1, minWidth: CHAT_MIN_WIDTH }}>
          <AIPanel
            hasWorkspace={!!sessions.currentWorkspace}
            onOpenWorkspace={handleOpenWorkspace}
            currentTaskSessionId={runningSessionId}
          />
        </div>

        {/* 编辑器：按需显示（有打开文件才出现）。
            树位置保持恒定（折叠时仅隐藏分隔条、宽度交给内容），
            避免折叠/展开切换导致 EditorArea 重建丢失已打开标签 */}
        <div style={{ display: editorCollapsed ? 'none' : 'flex', height: '100%', flexShrink: 0 }}>
          <Resizer direction="horizontal" onResize={handleEditorResize} />
        </div>
        <div
          style={{
            width: editorCollapsed ? undefined : editorWidth === 0 ? '40%' : editorWidth,
            flexShrink: 0,
            minWidth: editorCollapsed ? 0 : EDITOR_MIN,
            display: editorCollapsed ? 'none' : 'block',
          }}
        >
          <EditorArea
            ref={editorRef}
            collapsed={editorCollapsed}
            onToggleCollapse={toggleEditor}
            workspacePath={sessions.currentWorkspace?.path ?? null}
            toolTabsOpen={toolTabsOpen}
            activeToolId={activeToolId}
            onOpenTool={openTool}
            onCloseTool={closeTool}
            onActivateFile={activateFile}
          />
        </div>

        {/* 右缘图标轨：面板与编辑区的折叠态入口，始终可见 */}
        <IconRail
          activeToolId={activeToolId}
          editorCollapsed={editorCollapsed}
          onToolClick={handleRailToolClick}
          onToggleEditor={toggleEditor}
        />
      </div>

      {/* 状态信息已并入输入区底部行（ChatInput），无独立状态栏 */}

      {/* 设置 Modal */}
      <SettingsModal open={settingsOpen} onClose={() => setSettingsOpen(false)} onResetLayout={resetLayout} />
    </div>
  )
}

export default App
