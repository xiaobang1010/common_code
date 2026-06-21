import { useState, useRef } from 'react'
import ActivityBar from './components/ActivityBar'
import Sidebar from './components/Sidebar'
import EditorArea, { type EditorAreaHandle } from './components/EditorArea'
import AIPanel from './components/AIPanel'
import StatusBar from './components/StatusBar'
import { useChat } from './hooks/useChat'

// 活动栏可选的视图类型
export type ViewType = 'files' | 'search' | 'git' | 'settings'

function App() {
  // 活动栏当前选中的视图
  const [activeView, setActiveView] = useState<ViewType>('files')
  // 侧边栏是否折叠
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
  // AI 面板是否折叠
  const [aiPanelCollapsed, setAiPanelCollapsed] = useState(false)

  // 聊天状态提升到 App 层，方便 AIPanel 和 StatusBar 共享
  const chat = useChat()

  // 编辑区 ref，用于文件树点击打开文件
  const editorRef = useRef<EditorAreaHandle>(null)

  // 根据折叠状态计算各列宽度
  const gridColumns = [
    '48px',
    sidebarCollapsed ? '0px' : '240px',
    '1fr',
    aiPanelCollapsed ? '0px' : '420px',
  ].join(' ')

  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: gridColumns,
        gridTemplateRows: '1fr 24px',
        height: '100vh',
        width: '100vw',
        overflow: 'hidden',
      }}
    >
      <ActivityBar activeView={activeView} onViewChange={setActiveView} />
      <Sidebar
        activeView={activeView}
        collapsed={sidebarCollapsed}
        onToggleCollapse={() => setSidebarCollapsed(!sidebarCollapsed)}
        onFileOpen={(path) => editorRef.current?.openFile(path)}
      />
      <EditorArea ref={editorRef} />
      <AIPanel
        collapsed={aiPanelCollapsed}
        onToggleCollapse={() => setAiPanelCollapsed(!aiPanelCollapsed)}
        messages={chat.messages}
        isStreaming={chat.isStreaming}
        sendMessage={chat.sendMessage}
        tokenUsage={chat.tokenUsage}
        permissionRequest={chat.permissionRequest}
        resolvePermission={chat.resolvePermission}
      />
      {/* 状态栏横跨整行 */}
      <div style={{ gridColumn: '1 / -1' }}>
        <StatusBar
          tokenUsage={chat.tokenUsage}
          totalCost={chat.totalCost}
          isStreaming={chat.isStreaming}
          model={chat.model}
        />
      </div>
    </div>
  )
}

export default App
