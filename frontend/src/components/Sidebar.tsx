import SessionList from './sidebar/SessionList'
import type { SessionGroup } from '../api/client'

// 会话栏：左侧唯一的侧边区域，只承载会话列表
// 文件/搜索/Git 视图已移入右侧检查器面板，此处不再有视图切换
interface SidebarProps {
  collapsed: boolean
  onToggleCollapse: () => void
  // 会话列表相关 props
  groups: SessionGroup[]
  currentWorkspacePath: string | null
  currentSessionId: string | null
  onCreateSession: () => void
  onSwitchSession: (sessionId: string) => void
  onSwitchInWorkspace: (sessionId: string, workspacePath: string) => void
  onDeleteSession: (sessionId: string) => void
  onRemoveWorkspace: (workspacePath: string) => void
  onOpenWorkspace: () => void
}

function Sidebar({ collapsed, onToggleCollapse, groups, currentWorkspacePath, currentSessionId, onCreateSession, onSwitchSession, onSwitchInWorkspace, onDeleteSession, onRemoveWorkspace, onOpenWorkspace }: SidebarProps) {
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
          » 会话历史
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
        {/* 品牌标识（迁移自原活动栏） */}
        <div
          style={{
            width: '24px',
            height: '24px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            borderRadius: 'var(--radius-sm)',
            background: 'linear-gradient(135deg, var(--accent), #ff7a45)',
            color: '#1a1a1a',
            fontWeight: 700,
            fontSize: '12px',
            fontFamily: 'var(--font-display)',
            boxShadow: '0 2px 8px rgba(245, 166, 35, 0.3)',
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
          会话历史
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
            e.currentTarget.style.color = 'var(--accent)'
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
          onCreate={onCreateSession}
          onSwitch={onSwitchSession}
          onSwitchInWorkspace={onSwitchInWorkspace}
          onDelete={onDeleteSession}
          onRemoveWorkspace={onRemoveWorkspace}
          onOpenWorkspace={onOpenWorkspace}
        />
      </div>
    </div>
  )
}

export default Sidebar
