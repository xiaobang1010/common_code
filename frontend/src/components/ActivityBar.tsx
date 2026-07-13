import type { ViewType } from '../App'

// 精致的 SVG 图标，比 emoji 更专业统一
const viewIcons: Record<ViewType, React.ReactNode> = {
  files: (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z" />
    </svg>
  ),
  search: (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="11" cy="11" r="7" />
      <path d="m20 20-3.5-3.5" />
    </svg>
  ),
  git: (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="6" cy="6" r="2.5" />
      <circle cx="6" cy="18" r="2.5" />
      <circle cx="18" cy="12" r="2.5" />
      <path d="M6 8.5v7" />
      <path d="M6 12h7a3 3 0 0 0 2.5-2.5" />
    </svg>
  ),
  sessions: (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
    </svg>
  ),
}

const settingsIcon = (
  <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="12" cy="12" r="3" />
    <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
  </svg>
)

const viewLabels: Record<ViewType, string> = {
  files: '文件资源管理器',
  search: '搜索',
  git: '源代码管理',
  sessions: '会话历史',
}

interface ActivityBarProps {
  activeView: ViewType
  onViewChange: (view: ViewType) => void
  // settings 项点击打开设置 Modal，不再切侧边栏视图
  onOpenSettings: () => void
}

function ActivityBar({ activeView, onViewChange, onOpenSettings }: ActivityBarProps) {
  // 侧边栏视图：files / search / git / sessions
  const sidebarViews: ViewType[] = ['sessions', 'files', 'search', 'git']

  return (
    <div
      style={{
        width: '52px',
        height: '100%',
        backgroundColor: 'var(--bg-base)',
        borderRight: '1px solid var(--border-subtle)',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        paddingTop: '10px',
        gap: '4px',
      }}
    >
      {/* 顶部品牌标识 */}
      <div
        style={{
          width: '32px',
          height: '32px',
          marginBottom: '12px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          borderRadius: 'var(--radius-md)',
          background: 'linear-gradient(135deg, var(--accent), #ff7a45)',
          color: '#1a1a1a',
          fontWeight: 700,
          fontSize: '15px',
          fontFamily: 'var(--font-display)',
          boxShadow: '0 4px 12px rgba(245, 166, 35, 0.3)',
          letterSpacing: '-0.5px',
        }}
        title="Common Code"
      >
        C
      </div>

      {/* 侧边栏视图按钮 */}
      {sidebarViews.map((viewId) => {
        const isActive = activeView === viewId
        return (
          <button
            key={viewId}
            title={viewLabels[viewId]}
            onClick={() => onViewChange(viewId)}
            style={{
              width: '40px',
              height: '40px',
              border: 'none',
              background: isActive ? 'var(--accent-soft)' : 'transparent',
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: isActive ? 'var(--accent)' : 'var(--text-secondary)',
              borderRadius: 'var(--radius-md)',
              transition: 'all var(--transition-fast)',
              position: 'relative',
            }}
            onMouseEnter={(e) => {
              if (!isActive) {
                e.currentTarget.style.background = 'var(--bg-tertiary)'
                e.currentTarget.style.color = 'var(--text-primary)'
              }
            }}
            onMouseLeave={(e) => {
              if (!isActive) {
                e.currentTarget.style.background = 'transparent'
                e.currentTarget.style.color = 'var(--text-secondary)'
              }
            }}
          >
            {viewIcons[viewId]}
            {/* 激活指示条 - 左侧细线 */}
            {isActive && (
              <span
                style={{
                  position: 'absolute',
                  left: '-6px',
                  top: '50%',
                  transform: 'translateY(-50%)',
                  width: '3px',
                  height: '20px',
                  backgroundColor: 'var(--accent)',
                  borderRadius: '0 2px 2px 0',
                  boxShadow: '0 0 8px var(--accent-glow)',
                }}
              />
            )}
          </button>
        )
      })}

      {/* 分隔线 */}
      <div style={{ width: '24px', height: '1px', backgroundColor: 'var(--border-subtle)', margin: '8px 0' }} />

      {/* 设置按钮 — 打开 Modal */}
      <button
        title="设置"
        onClick={onOpenSettings}
        style={{
          width: '40px',
          height: '40px',
          border: 'none',
          background: 'transparent',
          cursor: 'pointer',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          color: 'var(--text-secondary)',
          borderRadius: 'var(--radius-md)',
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
        {settingsIcon}
      </button>
    </div>
  )
}

export default ActivityBar
