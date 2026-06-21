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
  // 折叠状态下只渲染一个空容器，不显示内容
  if (collapsed) {
    return <div style={{ backgroundColor: 'var(--bg-secondary)' }} />
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
          height: '36px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '0 8px',
          borderBottom: '1px solid var(--border)',
          flexShrink: 0,
        }}
      >
        <span
          style={{
            fontSize: '11px',
            textTransform: 'uppercase',
            color: 'var(--text-secondary)',
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
            color: 'var(--text-secondary)',
            cursor: 'pointer',
            fontSize: '14px',
            padding: '2px 4px',
          }}
        >
          «
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
