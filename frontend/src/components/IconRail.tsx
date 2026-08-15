import { TOOL_META, type ToolId } from './editor/toolMeta'

interface IconRailProps {
  // 当前激活工具标签：对应图标高亮
  activeToolId: ToolId | null
  // 编辑区展开状态：编辑区图标高亮
  editorCollapsed: boolean
  // 点图标直达对应面板、再点收起
  onToolClick: (id: ToolId) => void
  onToggleEditor: () => void
}

// 图标轨按钮统一样式
function railButtonStyle(active: boolean): React.CSSProperties {
  return {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    width: '28px',
    height: '28px',
    border: '1px solid',
    borderColor: active ? 'var(--border-strong)' : 'transparent',
    borderRadius: 'var(--radius-sm)',
    background: active ? 'var(--selected-bg)' : 'transparent',
    color: active ? 'var(--text-primary)' : 'var(--text-secondary)',
    cursor: 'pointer',
    transition: 'all var(--transition-fast)',
    position: 'relative',
    flexShrink: 0,
  }
}

// 右缘图标轨：扩展面板与编辑区的折叠态入口（36-44px 极窄）
// 一个图标对应一个面板，点图标直达、再点收回；激活图标带状态点
function IconRail({ activeToolId, editorCollapsed, onToolClick, onToggleEditor }: IconRailProps) {
  return (
    <div
      style={{
        width: '40px',
        flexShrink: 0,
        height: '100%',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        gap: '4px',
        paddingTop: '8px',
        backgroundColor: 'var(--bg-base)',
        borderLeft: '1px solid var(--border)',
        overflow: 'hidden',
      }}
    >
      {TOOL_META.map(({ id, title, icon }) => {
        const active = activeToolId === id
        return (
          <button
            key={id}
            onClick={() => onToolClick(id)}
            title={title}
            style={railButtonStyle(active)}
            onMouseEnter={(e) => {
              if (!active) {
                e.currentTarget.style.borderColor = 'var(--border)'
                e.currentTarget.style.color = 'var(--text-primary)'
              }
            }}
            onMouseLeave={(e) => {
              if (!active) {
                e.currentTarget.style.borderColor = 'transparent'
                e.currentTarget.style.color = 'var(--text-secondary)'
              }
            }}
          >
            {icon}
            {/* 激活状态点 */}
            {active && (
              <span
                style={{
                  position: 'absolute',
                  left: '2px',
                  top: '2px',
                  width: '5px',
                  height: '5px',
                  borderRadius: '50%',
                  backgroundColor: 'var(--text-primary)',
                }}
              />
            )}
          </button>
        )
      })}

      {/* 分隔线 */}
      <div style={{ width: '20px', height: '1px', backgroundColor: 'var(--border)', margin: '4px 0', flexShrink: 0 }} />

      {/* 编辑区展开/收起图标：原「» 编辑器」竖条并入此轨 */}
      <button
        onClick={onToggleEditor}
        title={editorCollapsed ? '展开编辑区' : '收起编辑区'}
        style={railButtonStyle(!editorCollapsed)}
        onMouseEnter={(e) => {
          if (editorCollapsed) {
            e.currentTarget.style.borderColor = 'var(--border)'
            e.currentTarget.style.color = 'var(--text-primary)'
          }
        }}
        onMouseLeave={(e) => {
          if (editorCollapsed) {
            e.currentTarget.style.borderColor = 'transparent'
            e.currentTarget.style.color = 'var(--text-secondary)'
          }
        }}
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
          <path d="M14 2v6h6" />
        </svg>
      </button>
    </div>
  )
}

export default IconRail
