import { useState, useRef, useCallback, useEffect, useMemo } from 'react'
import Sidebar from './components/Sidebar'
import EditorArea, { type EditorAreaHandle } from './components/EditorArea'
import AIPanel from './components/AIPanel'
import TitleBar from './components/TitleBar'
import InspectorPanel, { type CardId, type PanelMode } from './components/inspector/InspectorPanel'
import Resizer from './components/Resizer'
import SettingsModal from './components/settings/SettingsModal'
import WorkspaceSelector from './components/ai/WorkspaceSelector'
import BranchSelector from './components/ai/BranchSelector'
import { useChatStore } from './stores/useChatStore'
import { useSettingsStore } from './stores/useSettingsStore'
import { useSessions } from './hooks/useSessions'
import { gitApi } from './api/client'

// 面板宽度范围约束
const SIDEBAR_MIN = 180
const SIDEBAR_MAX = 500
const EDITOR_MIN = 200
const EDITOR_MAX_RATIO = 0.85 // 编辑器最多占窗口 85%

function App() {
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
  // 编辑器按需显示：默认隐藏，打开文件时才出现
  const [editorCollapsed, setEditorCollapsed] = useState(true)

  // 右侧检查器面板（概要/终端/文件/审查卡片）显隐，默认隐藏
  const [inspectorVisible, setInspectorVisible] = useState(false)
  // 检查器面板形态与选中卡：由 App 持有，面板隐藏再打开时能恢复
  const [inspectorMode, setInspectorMode] = useState<PanelMode>('list')
  const [inspectorCard, setInspectorCard] = useState<CardId>('summary')
  // 面板是否被打开过：首次打开后保持挂载（隐藏仅 CSS 隐藏），终端会话不丢
  const [inspectorEverShown, setInspectorEverShown] = useState(false)

  // 设置 Modal 开关
  const [settingsOpen, setSettingsOpen] = useState(false)

  // 侧边栏和编辑器的像素宽度（折叠时不起作用，恢复时用得上）
  const [sidebarWidth, setSidebarWidth] = useState(240)
  const [editorWidth, setEditorWidth] = useState(0) // 0 表示用 flex 比例

  // 聊天状态从 store 订阅：action 引用稳定，不会因流式更新引起本组件重渲
  const chatSessionId = useChatStore(s => s.sessionId)
  const setSessionId = useChatStore(s => s.setSessionId)
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
    })
  }, [sessions.currentSessionId]) // eslint-disable-line react-hooks/exhaustive-deps

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
      // 第一次展开时如果没设过宽度，给个默认值（窗口的 40%）
      if (!prev && editorWidth === 0) {
        setEditorWidth(Math.floor(window.innerWidth * 0.4))
      }
      return !prev
    })
  }, [editorWidth])

  // 显隐右侧检查器面板
  const toggleInspector = useCallback(() => {
    setInspectorVisible((prev) => !prev)
    setInspectorEverShown(true)
  }, [])

  // 进入聚焦态：选中卡片并切换形态
  const enterInspectorFocus = useCallback((id: CardId) => {
    setInspectorCard(id)
    setInspectorMode('focus')
  }, [])

  // 打开搜索：显示检查器并切到搜索 tab（复用检查器 SearchPanel）
  const handleOpenSearch = useCallback(() => {
    setInspectorVisible(true)
    setInspectorEverShown(true)
    enterInspectorFocus('search')
  }, [enterInspectorFocus])

  // ---- 会话管理相关回调 ----

  // 新建会话：创建后设为当前，清空消息
  const handleCreateSession = useCallback(async () => {
    const id = await sessions.createSession()
    if (id) {
      setSessionId(id)
      clearMessages()
    }
  }, [sessions, setSessionId, clearMessages])

  // 切换会话：加载消息到聊天面板
  const handleSwitchSession = useCallback(async (sessionId: string) => {
    const messages = await sessions.switchSession(sessionId)
    setSessionId(sessionId)
    if (messages) {
      loadMessages(messages)
    } else {
      clearMessages()
    }
  }, [sessions, setSessionId, loadMessages, clearMessages])

  // 删除会话：如果删的是当前会话，加载新的当前会话
  const handleDeleteSession = useCallback(async (sessionId: string) => {
    const wasCurrent = sessionId === chatSessionId
    const newSessionId = await sessions.deleteSession(sessionId)
    if (wasCurrent) {
      setSessionId(newSessionId)
      if (newSessionId) {
        // 加载新当前会话的消息
        const messages = await sessions.switchSession(newSessionId)
        if (messages) loadMessages(messages)
        else clearMessages()
      } else {
        clearMessages()
      }
    }
  }, [sessions, setSessionId, loadMessages, clearMessages])

  // 跨工作区切换会话：可能需要先切换工作区
  const handleSwitchInWorkspace = useCallback(async (sessionId: string, workspacePath: string) => {
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
  }, [sessions, setSessionId, loadMessages, clearMessages])

  // 切换工作区：更新分支，加载新当前会话
  const handleSwitchWorkspace = useCallback(async (path: string) => {
    const result = await sessions.switchWorkspace(path)
    if (result) {
      setCurrentBranch(result.branch)
      // 加载新当前会话的消息
      if (result.sessionId) {
        const messages = await sessions.switchSession(result.sessionId)
        setSessionId(result.sessionId)
        if (messages) loadMessages(messages)
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

  // 移除工作区：删除后刷新列表，如果删的是当前工作区则清空聊天状态
  const handleRemoveWorkspace = useCallback(async (workspacePath: string) => {
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

  // 全局快捷键：Ctrl/⌘+N 新建任务，Ctrl/⌘+K 搜索
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
        handleOpenSearch()
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [handleCreateSession, handleOpenSearch])

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
        inspectorVisible={inspectorVisible}
        onToggleInspector={toggleInspector}
        onOpenSettings={() => setSettingsOpen(true)}
        onNewSession={handleCreateSession}
        currentTaskTitle={currentTaskTitle}
      />

      {/* 主体行：会话栏 + AI面板 + 编辑器 + 检查器面板 */}
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
                onOpenSearch={handleOpenSearch}
                runningSessionId={sessions.currentTask?.session_id ?? null}
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
            onOpenSearch={handleOpenSearch}
            runningSessionId={sessions.currentTask?.session_id ?? null}
            onRenameSession={sessions.renameSession}
            onToggleSessionPin={sessions.toggleSessionPin}
            onUpdateWorkspace={sessions.updateWorkspaceMeta}
          />
        )}

        {/* AI 面板：占据剩余空间（主角） */}
        <div style={{ flex: 1, minWidth: 0 }}>
          <AIPanel
            hasWorkspace={!!sessions.currentWorkspace}
            onOpenWorkspace={handleOpenWorkspace}
            currentTaskSessionId={sessions.currentTask?.session_id ?? null}
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
          }}
        >
          <EditorArea
            ref={editorRef}
            collapsed={editorCollapsed}
            onToggleCollapse={toggleEditor}
          />
        </div>

        {/* 右侧检查器面板：首次打开后保持挂载，隐藏时仅 CSS 隐藏（保活终端会话） */}
        {(inspectorVisible || inspectorEverShown) && (
          <div style={{ display: inspectorVisible ? 'flex' : 'none', height: '100%', flexShrink: 0 }}>
            <InspectorPanel
              onFileOpen={(path) => editorRef.current?.openFile(path)}
              workspacePath={sessions.currentWorkspace?.path ?? null}
              mode={inspectorMode}
              activeCard={inspectorCard}
              onEnterFocus={enterInspectorFocus}
              onBackToList={() => setInspectorMode('list')}
              onCardChange={setInspectorCard}
            />
          </div>
        )}
      </div>

      {/* 状态信息已并入输入区底部行（ChatInput），无独立状态栏 */}

      {/* 设置 Modal */}
      <SettingsModal open={settingsOpen} onClose={() => setSettingsOpen(false)} />
    </div>
  )
}

export default App
