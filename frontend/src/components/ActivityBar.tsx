import type { ViewType } from '../App'

// 活动栏的视图配置：图标和对应视图
const views: { id: ViewType; icon: string; label: string }[] = [
  { id: 'files', icon: '📁', label: '文件' },
  { id: 'search', icon: '🔍', label: '搜索' },
  { id: 'git', icon: '🌿', label: 'Git' },
  { id: 'settings', icon: '⚙️', label: '设置' },
]

interface ActivityBarProps {
  activeView: ViewType
  onViewChange: (view: ViewType) => void
}

function ActivityBar({ activeView, onViewChange }: ActivityBarProps) {
  return (
    <div
      style={{
        width: '48px',
        height: '100%',
        backgroundColor: 'var(--bg-secondary)',
        borderRight: '1px solid var(--border)',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        paddingTop: '8px',
      }}
    >
      {views.map((view) => {
        const isActive = activeView === view.id
        return (
          <button
            key={view.id}
            title={view.label}
            onClick={() => onViewChange(view.id)}
            style={{
              width: '48px',
              height: '48px',
              border: 'none',
              background: 'transparent',
              cursor: 'pointer',
              fontSize: '20px',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: isActive ? 'var(--text-primary)' : 'var(--text-secondary)',
              // 当前选中视图左侧显示蓝色边框
              borderLeft: isActive
                ? '2px solid var(--accent)'
                : '2px solid transparent',
            }}
          >
            {view.icon}
          </button>
        )
      })}
    </div>
  )
}

export default ActivityBar
