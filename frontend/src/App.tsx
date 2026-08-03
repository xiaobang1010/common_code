import { useState, useRef, useCallback, useEffect } from 'react'
import ActivityBar from './components/ActivityBar'
import Sidebar from './components/Sidebar'
import EditorArea, { type EditorAreaHandle } from './components/EditorArea'
import AIPanel from './components/AIPanel'
import StatusBar from './components/StatusBar'
import Resizer from './components/Resizer'
import SettingsModal from './components/settings/SettingsModal'
import WorkspaceSelector from './components/ai/WorkspaceSelector'
import BranchSelector from './components/ai/BranchSelector'
import { useChat } from './hooks/useChat'
import { useSessions } from './hooks/useSessions'
import { gitApi } from './api/client'

// 活动栏可选的视图类型（设置已升级为独立 Modal，不再走侧边栏视图）
export type ViewType = 'files' | 'search' | 'git' | 'sessions'

// 面板宽度范围约束
const SIDEBAR_MIN = 180
const SIDEBAR_MAX = 500
const EDITOR_MIN = 200
const EDITOR_MAX_RATIO = 0.85 // 编辑器最多占窗口 85%

function App() {
  const [activeView, setActiveView] = useState<ViewType>('sessions')
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
  const [editorCollapsed, setEditorCollapsed] = useState(false)

  // 设置 Modal 开关
  const [settingsOpen, setSettingsOpen] = useState(false)

  // 侧边栏和编辑器的像素宽度（折叠时不起作用，恢复时用得上）
  const [sidebarWidth, setSidebarWidth] = useState(240)
  const [editorWidth, setEditorWidth] = useState(0) // 0 表示用 flex 比例

  const chat = useChat()
  const sessions = useSessions()
  const editorRef = useRef<EditorAreaHandle>(null)

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

  // 初始会话加载：第一次有会话时加载消息
  useEffect(() => {
    if (initialSessionLoaded.current) return
    if (!sessions.currentSessionId) return
    initialSessionLoaded.current = true
    const id = sessions.currentSessionId
    sessions.switchSession(id).then(messages => {
      chat.setSessionId(id)
      if (messages) chat.loadMessages(messages)
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

  // ---- 会话管理相关回调 ----

  // 新建会话：创建后设为当前，清空消息
  const handleCreateSession = useCallback(async () => {
    const id = await sessions.createSession()
    if (id) {
      chat.setSessionId(id)
      chat.clearMessages()
    }
  }, [sessions, chat])

  // 切换会话：加载消息到聊天面板
  const handleSwitchSession = useCallback(async (sessionId: string) => {
    const messages = await sessions.switchSession(sessionId)
    chat.setSessionId(sessionId)
    if (messages) {
      chat.loadMessages(messages)
    } else {
      chat.clearMessages()
    }
  }, [sessions, chat])

  // 删除会话：如果删的是当前会话，加载新的当前会话
  const handleDeleteSession = useCallback(async (sessionId: string) => {
    const wasCurrent = sessionId === chat.sessionId
    const newSessionId = await sessions.deleteSession(sessionId)
    if (wasCurrent) {
      chat.setSessionId(newSessionId)
      if (newSessionId) {
        // 加载新当前会话的消息
        const messages = await sessions.switchSession(newSessionId)
        if (messages) chat.loadMessages(messages)
        else chat.clearMessages()
      } else {
        chat.clearMessages()
      }
    }
  }, [sessions, chat])

  // 跨工作区切换会话：可能需要先切换工作区
  const handleSwitchInWorkspace = useCallback(async (sessionId: string, workspacePath: string) => {
    const result = await sessions.switchToSessionInWorkspace(sessionId, workspacePath)
    if (result) {
      chat.setSessionId(sessionId)
      if (result.messages) {
        chat.loadMessages(result.messages)
      } else {
        chat.clearMessages()
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
  }, [sessions, chat])

  // 切换工作区：更新分支，加载新当前会话
  const handleSwitchWorkspace = useCallback(async (path: string) => {
    const result = await sessions.switchWorkspace(path)
    if (result) {
      setCurrentBranch(result.branch)
      // 加载新当前会话的消息
      if (result.sessionId) {
        const messages = await sessions.switchSession(result.sessionId)
        chat.setSessionId(result.sessionId)
        if (messages) chat.loadMessages(messages)
      } else {
        chat.setSessionId(null)
        chat.clearMessages()
      }
    }
  }, [sessions, chat])

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
      chat.setSessionId(null)
      chat.clearMessages()
      setCurrentBranch('')
      setBranches([])
    }
  }, [sessions, chat])

  // 打开工作区：弹出目录选择对话框
  const handleOpenWorkspace = useCallback(async () => {
    const w = window as unknown as { electronAPI?: { selectDirectory?: () => Promise<string | null> } }
    const path = await w.electronAPI?.selectDirectory?.()
    if (path) {
      handleSwitchWorkspace(path)
    }
  }, [handleSwitchWorkspace])

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
      {/* 主体行：活动栏 + 侧边栏 + AI面板 + 编辑器 */}
      <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
        <ActivityBar
          activeView={activeView}
          onViewChange={setActiveView}
          onOpenSettings={() => setSettingsOpen(true)}
        />

        {/* 侧边栏：折叠时不占位，展开时固定宽度 + 可拖拽 */}
        {!sidebarCollapsed && (
          <>
            <div style={{ width: sidebarWidth, flexShrink: 0 }}>
              <Sidebar
                activeView={activeView}
                collapsed={false}
                onToggleCollapse={toggleSidebar}
                onFileOpen={(path) => editorRef.current?.openFile(path)}
                groups={sessions.allGroups}
                currentWorkspacePath={sessions.currentWorkspace?.path ?? null}
                currentSessionId={sessions.currentSessionId}
                onCreateSession={handleCreateSession}
                onSwitchSession={handleSwitchSession}
                onSwitchInWorkspace={handleSwitchInWorkspace}
                onDeleteSession={handleDeleteSession}
                onRemoveWorkspace={handleRemoveWorkspace}
                onOpenWorkspace={handleOpenWorkspace}
              />
            </div>
            <Resizer direction="horizontal" onResize={handleSidebarResize} />
          </>
        )}
        {sidebarCollapsed && (
          <Sidebar
            activeView={activeView}
            collapsed={true}
            onToggleCollapse={toggleSidebar}
            onFileOpen={(path) => editorRef.current?.openFile(path)}
            groups={sessions.allGroups}
            currentWorkspacePath={sessions.currentWorkspace?.path ?? null}
            currentSessionId={sessions.currentSessionId}
            onCreateSession={handleCreateSession}
            onSwitchSession={handleSwitchSession}
            onSwitchInWorkspace={handleSwitchInWorkspace}
            onDeleteSession={handleDeleteSession}
            onRemoveWorkspace={handleRemoveWorkspace}
            onOpenWorkspace={handleOpenWorkspace}
          />
        )}

        {/* AI 面板：占据剩余空间（主角） */}
        <div style={{ flex: 1, minWidth: 0 }}>
          <AIPanel
            blocks={chat.blocks}
            formatDuration={chat.formatDuration}
            isStreaming={chat.isStreaming}
            sendMessage={chat.sendMessage}
            abort={chat.abort}
            tokenUsage={chat.tokenUsage}
            permissionRequest={chat.permissionRequest}
            resolvePermission={chat.resolvePermission}
            permissionMode={chat.permissionMode}
            onPermissionModeChange={chat.setPermissionMode}
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
            onNewSession={handleCreateSession}
          />
        </div>

        {/* 编辑器：折叠时不占位，展开时固定宽度 + 可拖拽 */}
        {editorCollapsed ? (
          <EditorArea
            ref={editorRef}
            collapsed={true}
            onToggleCollapse={toggleEditor}
          />
        ) : (
          <>
            <Resizer direction="horizontal" onResize={handleEditorResize} />
            <div
              style={{
                width: editorWidth === 0 ? '40%' : editorWidth,
                flexShrink: 0,
                minWidth: EDITOR_MIN,
              }}
            >
              <EditorArea
                ref={editorRef}
                collapsed={false}
                onToggleCollapse={toggleEditor}
              />
            </div>
          </>
        )}
      </div>

      {/* 状态栏 */}
      <StatusBar
        tokenUsage={chat.tokenUsage}
        totalCost={chat.totalCost}
        isStreaming={chat.isStreaming}
        model={chat.model}
      />

      {/* 设置 Modal */}
      <SettingsModal open={settingsOpen} onClose={() => setSettingsOpen(false)} />
    </div>
  )
}

export default App
