import SessionList from './sidebar/SessionList'
import type { SessionGroup } from '../api/client'

// 入口区按钮统一样式（竖排：图标 + 文字居左，快捷键居右，无边框）
const entryButtonStyle: React.CSSProperties = {
  width: '100%',
  display: 'flex',
  alignItems: 'center',
  gap: '8px',
  padding: '7px 10px',
  border: 'none',
  borderRadius: 'var(--radius-sm)',
  background: 'transparent',
  color: 'var(--text-secondary)',
  fontSize: '12px',
  fontFamily: 'var(--font-ui)',
  cursor: 'pointer',
  transition: 'all var(--transition-fast)',
  textAlign: 'left',
}

// 快捷键提示统一样式（居右）
const entryShortcutStyle: React.CSSProperties = {
  marginLeft: 'auto',
  fontSize: '10px',
  fontFamily: 'var(--font-mono)',
  color: 'var(--text-tertiary)',
  letterSpacing: '0.3px',
}

// 会话栏：左侧唯一的侧边区域，只承载会话列表
// 文件视图在编辑区右缘树窄列、搜索/审查为编辑区工具标签，此处不再有视图切换
interface SidebarProps {
  collapsed: boolean
  onToggleCollapse: () => void
  // 会话列表相关 props
  groups: SessionGroup[]
  currentWorkspacePath: string | null
  currentSessionId: string | null
  // 当前运行任务所属会话（列表显示运行指示）
  runningSessionId: string | null
  onCreateSession: () => void
  onSwitchSession: (sessionId: string) => void
  onSwitchInWorkspace: (sessionId: string, workspacePath: string) => void
  onDeleteSession: (sessionId: string) => void
  onRemoveWorkspace: (workspacePath: string) => void
  onOpenWorkspace: () => void
  // 打开搜索：打开搜索工具标签
  onOpenSearch: () => void
  onRenameSession: (sessionId: string, title: string) => void
  onToggleSessionPin: (sessionId: string, pinned: boolean) => void
  onUpdateWorkspace: (path: string, data: { alias?: string; pinned?: boolean }) => void
}

function Sidebar({ collapsed, onToggleCollapse, groups, currentWorkspacePath, currentSessionId, runningSessionId, onCreateSession, onSwitchSession, onSwitchInWorkspace, onDeleteSession, onRemoveWorkspace, onOpenWorkspace, onOpenSearch, onRenameSession, onToggleSessionPin, onUpdateWorkspace }: SidebarProps) {
  // 折叠状态下渲染一个窄条展开按钮
  if (collapsed) {
    return (
      <div
        style={{
          backgroundColor: 'var(--bg-base)',
          borderRight: '1px solid var(--border-subtle)',
          height: '100%',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          cursor: 'pointer',
          transition: 'background var(--transition-fast)',
        }}
        onClick={onToggleCollapse}
        title="展开会话栏"
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
          » 项目
        </span>
      </div>
    )
  }

  return (
    <div
      style={{
        backgroundColor: 'var(--bg-secondary)',
        borderRight: '1px solid var(--border)',
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        overflow: 'hidden',
      }}
    >
      {/* 顶部标题栏：品牌标识 + 标题 + 折叠按钮 */}
      <div
        style={{
          height: '44px',
          display: 'flex',
          alignItems: 'center',
          gap: '10px',
          padding: '0 8px 0 12px',
          borderBottom: '1px solid var(--border)',
          flexShrink: 0,
        }}
      >
        {/* 品牌标识（迁移自原活动栏）：单色字形小方块，无渐变无彩色 */}
        <div
          style={{
            width: '24px',
            height: '24px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            borderRadius: 'var(--radius-sm)',
            background: 'var(--bg-elevated)',
            border: '1px solid var(--border-strong)',
            color: 'var(--text-primary)',
            fontWeight: 700,
            fontSize: '12px',
            fontFamily: 'var(--font-display)',
            boxShadow: '0 1px 2px rgba(0, 0, 0, 0.3)',
            letterSpacing: '-0.5px',
            flexShrink: 0,
          }}
          title="Common Code"
        >
          C
        </div>
        <span
          style={{
            fontSize: '11px',
            textTransform: 'uppercase',
            color: 'var(--text-secondary)',
            fontFamily: 'var(--font-ui)',
            fontWeight: 600,
            letterSpacing: '1.2px',
            flex: 1,
          }}
        >
          项目
        </span>
        <button
          onClick={onToggleCollapse}
          title="折叠会话栏"
          style={{
            border: 'none',
            background: 'transparent',
            color: 'var(--text-tertiary)',
            cursor: 'pointer',
            padding: '4px 6px',
            borderRadius: 'var(--radius-sm)',
            transition: 'all var(--transition-fast)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
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
            <path d="M15 6l-6 6 6 6" />
          </svg>
        </button>
      </div>
      {/* 常驻入口区：新建任务 / 搜索 / 打开工作区（竖排，分组折叠与否均可见） */}
      <div
        style={{
          display: 'flex',
          flexDirection: 'column',
          gap: '6px',
          padding: '8px',
          borderBottom: '1px solid var(--border)',
          flexShrink: 0,
        }}
      >
        <button
          onClick={onCreateSession}
          title="新建任务 (Ctrl+N)"
          style={entryButtonStyle}
          onMouseEnter={(e) => {
            e.currentTarget.style.color = 'var(--text-primary)'
            e.currentTarget.style.backgroundColor = 'var(--hover-bg)'
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.color = 'var(--text-secondary)'
            e.currentTarget.style.backgroundColor = 'transparent'
          }}
        >
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M12 5v14M5 12h14" />
          </svg>
          新建任务
          <span style={entryShortcutStyle}>Ctrl+N</span>
        </button>
        <button
          onClick={onOpenSearch}
          title="搜索 (Ctrl+K)"
          style={entryButtonStyle}
          onMouseEnter={(e) => {
            e.currentTarget.style.color = 'var(--text-primary)'
            e.currentTarget.style.backgroundColor = 'var(--hover-bg)'
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.color = 'var(--text-secondary)'
            e.currentTarget.style.backgroundColor = 'transparent'
          }}
        >
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="11" cy="11" r="7" />
            <path d="M21 21l-4.3-4.3" />
          </svg>
          搜索
          <span style={entryShortcutStyle}>Ctrl+K</span>
        </button>
        <button
          onClick={onOpenWorkspace}
          title="打开工作区"
          style={entryButtonStyle}
          onMouseEnter={(e) => {
            e.currentTarget.style.color = 'var(--text-primary)'
            e.currentTarget.style.backgroundColor = 'var(--hover-bg)'
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.color = 'var(--text-secondary)'
            e.currentTarget.style.backgroundColor = 'transparent'
          }}
        >
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z" />
          </svg>
          打开工作区
        </button>
      </div>
      {/* 会话列表 */}
      <div
        style={{
          flex: 1,
          overflow: 'hidden',
          display: 'flex',
          flexDirection: 'column',
        }}
      >
        <SessionList
          groups={groups}
          currentWorkspacePath={currentWorkspacePath}
          currentSessionId={currentSessionId}
          runningSessionId={runningSessionId}
          onCreate={onCreateSession}
          onSwitch={onSwitchSession}
          onSwitchInWorkspace={onSwitchInWorkspace}
          onDelete={onDeleteSession}
          onRemoveWorkspace={onRemoveWorkspace}
          onOpenWorkspace={onOpenWorkspace}
          onRename={onRenameSession}
          onTogglePin={onToggleSessionPin}
          onUpdateWorkspace={onUpdateWorkspace}
        />
      </div>
    </div>
  )
}

export default Sidebar
