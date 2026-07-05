import { useState, useRef, useCallback } from 'react'
import ActivityBar from './components/ActivityBar'
import Sidebar from './components/Sidebar'
import EditorArea, { type EditorAreaHandle } from './components/EditorArea'
import AIPanel from './components/AIPanel'
import StatusBar from './components/StatusBar'
import Resizer from './components/Resizer'
import SettingsModal from './components/settings/SettingsModal'
import { useChat } from './hooks/useChat'

// 活动栏可选的视图类型（设置已升级为独立 Modal，不再走侧边栏视图）
export type ViewType = 'files' | 'search' | 'git'

// 面板宽度范围约束
const SIDEBAR_MIN = 180
const SIDEBAR_MAX = 500
const EDITOR_MIN = 200
const EDITOR_MAX_RATIO = 0.85 // 编辑器最多占窗口 85%

function App() {
  const [activeView, setActiveView] = useState<ViewType>('files')
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
  const [editorCollapsed, setEditorCollapsed] = useState(false)

  // 设置 Modal 开关
  const [settingsOpen, setSettingsOpen] = useState(false)

  // 侧边栏和编辑器的像素宽度（折叠时不起作用，恢复时用得上）
  const [sidebarWidth, setSidebarWidth] = useState(240)
  const [editorWidth, setEditorWidth] = useState(0) // 0 表示用 flex 比例

  const chat = useChat()
  const editorRef = useRef<EditorAreaHandle>(null)

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
          />
        )}

        {/* AI 面板：占据剩余空间（主角） */}
        <div style={{ flex: 1, minWidth: 0 }}>
          <AIPanel
            messages={chat.messages}
            isStreaming={chat.isStreaming}
            sendMessage={chat.sendMessage}
            abort={chat.abort}
            tokenUsage={chat.tokenUsage}
            permissionRequest={chat.permissionRequest}
            resolvePermission={chat.resolvePermission}
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
