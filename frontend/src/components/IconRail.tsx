import { useState } from 'react'
import { TOOL_META, type ToolId } from './editor/toolMeta'

interface IconRailProps {
  // 点图标直达对应工具标签：展开面板并激活该标签
  onToolClick: (id: ToolId) => void
}

// 卡片内图标按钮统一样式
function cardButtonStyle(hovered: boolean): React.CSSProperties {
  return {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    width: '28px',
    height: '28px',
    border: 'none',
    borderRadius: 'var(--radius-sm)',
    background: hovered ? 'var(--bg-base)' : 'transparent',
    color: hovered ? 'var(--text-primary)' : 'var(--text-secondary)',
    cursor: 'pointer',
    transition: 'all var(--transition-fast)',
  }
}

// 收起态入口：右上角圆润悬浮卡片（替代原右缘全高图标轨）。
// 固定在标题栏下方右上角，图标竖排卡内；hover/focus 时图标左侧浮出文字气泡。
// 面板展开时由 App 决定不渲染本卡片。
function IconRail({ onToolClick }: IconRailProps) {
  const [hovered, setHovered] = useState<ToolId | null>(null)

  return (
    <div
      style={{
        position: 'fixed',
        top: '44px', // 标题栏 38px 下方留出间距，避免遮挡标题栏交互区
        right: '14px',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'stretch',
        gap: '2px',
        padding: '6px',
        borderRadius: 'var(--radius-lg)',
        backgroundColor: 'var(--bg-elevated)',
        boxShadow: 'var(--shadow-md)',
        border: '1px solid var(--border-subtle)',
        zIndex: 40,
        userSelect: 'none',
      }}
    >
      {/* railHidden 条目（如搜索）不在卡片中呈现：入口冗余，功能由侧栏/Ctrl+K 承载 */}
      {TOOL_META.filter((t) => !t.railHidden).map(({ id, title, icon }) => (
        <button
          key={id}
          onClick={() => onToolClick(id)}
          aria-label={title}
          title={title}
          onMouseEnter={() => setHovered(id)}
          onMouseLeave={() => setHovered(null)}
          onFocus={() => setHovered(id)}
          onBlur={() => setHovered(null)}
          style={{ ...cardButtonStyle(hovered === id), position: 'relative' }}
        >
          {icon}
          {/* 文字气泡：hover/focus 时浮在图标左侧 */}
          {hovered === id && (
            <span
              style={{
                position: 'absolute',
                right: '100%',
                marginRight: '8px',
                whiteSpace: 'nowrap',
                fontSize: '11px',
                fontFamily: 'var(--font-ui)',
                color: 'var(--text-primary)',
                padding: '2px 8px',
                borderRadius: 'var(--radius-sm)',
                backgroundColor: 'var(--bg-elevated)',
                border: '1px solid var(--border)',
                boxShadow: 'var(--shadow-sm)',
                pointerEvents: 'none',
              }}
            >
              {title}
            </span>
          )}
        </button>
      ))}
    </div>
  )
}

export default IconRail
