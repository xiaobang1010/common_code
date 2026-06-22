import type { ViewType } from '../App'
import FileTree from './sidebar/FileTree'
import GitStatus from './sidebar/GitStatus'
import SearchPanel from './sidebar/SearchPanel'
import SettingsPanel from './sidebar/SettingsPanel'

// 各视图对应的标题
const viewTitles: Record<ViewType, string> = {
  files: '文件资源管理器',
  search: '搜索',
  git: '源代码管理',
  settings: '设置',
}

interface SidebarProps {
  activeView: ViewType
  collapsed: boolean
  onToggleCollapse: () => void
  onFileOpen: (path: string) => void
}

function Sidebar({ activeView, collapsed, onToggleCollapse, onFileOpen }: SidebarProps) {
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
        title="展开侧边栏"
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
          » {viewTitles[activeView]}
        </span>
      </div>
    )
  }

  // 根据活动视图渲染对应内容
  const renderContent = () => {
    switch (activeView) {
      case 'files':
        return <FileTree onFileOpen={onFileOpen} />
      case 'search':
        return <SearchPanel onFileOpen={onFileOpen} />
      case 'git':
        return <GitStatus />
      case 'settings':
        return <SettingsPanel />
    }
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
      {/* 顶部标题栏 + 折叠按钮 */}
      <div
        style={{
          height: '44px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '0 8px 0 16px',
          borderBottom: '1px solid var(--border)',
          flexShrink: 0,
        }}
      >
        <span
          style={{
            fontSize: '11px',
            textTransform: 'uppercase',
            color: 'var(--text-secondary)',
            fontFamily: 'var(--font-ui)',
            fontWeight: 600,
            letterSpacing: '1.2px',
          }}
        >
          {viewTitles[activeView]}
        </span>
        <button
          onClick={onToggleCollapse}
          title="折叠侧边栏"
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
      {/* 视图内容区 */}
      <div
        style={{
          flex: 1,
          overflow: 'hidden',
          display: 'flex',
          flexDirection: 'column',
        }}
      >
        {renderContent()}
      </div>
    </div>
  )
}

export default Sidebar
