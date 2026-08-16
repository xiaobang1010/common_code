import type { MouseEvent } from 'react'

interface Tab {
  path: string
  name: string
  dirty: boolean
}

interface TabsProps {
  tabs: Tab[]
  activePath: string
  onSwitch: (path: string) => void
  onClose: (path: string) => void
  // 关闭全部标签页：由 EditorArea 的批量关闭逻辑承接
  onCloseAll: () => void
  // 标签右键：交给父层弹上下文菜单
  onContextMenuTab: (e: MouseEvent, path: string) => void
}

// 标签页栏：内部可水平滚动，中键关闭，当前激活标签底部高亮
// 同名文件（不同目录）用父目录后缀区分
// 「关闭全部」按钮在滚动容器之外固定可见，标签多时不随滚动消失
function Tabs({ tabs, activePath, onSwitch, onClose, onCloseAll, onContextMenuTab }: TabsProps) {
  // 统计同名文件数量
  const nameCounts = new Map<string, number>()
  for (const t of tabs) {
    nameCounts.set(t.name, (nameCounts.get(t.name) || 0) + 1)
  }
  // 同名文件显示「父目录/文件名」，其余只显示文件名
  const displayName = (tab: Tab): string => {
    if ((nameCounts.get(tab.name) || 0) < 2) return tab.name
    const segs = tab.path.split('/').filter(Boolean)
    return segs.length >= 2 ? `${segs[segs.length - 2]}/${tab.name}` : tab.name
  }

  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'stretch',
        backgroundColor: 'var(--bg-primary)',
        height: '38px',
        flexShrink: 0,
        flex: '0 1 auto',
        minWidth: 0,
        borderBottom: '1px solid var(--border)',
      }}
    >
      <div style={{ display: 'flex', overflowX: 'auto', height: '100%' }}>
        {tabs.map((tab) => {
          const active = tab.path === activePath
          return (
            <div
              key={tab.path}
              onClick={() => onSwitch(tab.path)}
              onMouseDown={(e) => {
                if (e.button === 1) {
                  e.preventDefault()
                  onClose(tab.path)
                }
              }}
              onContextMenu={(e) => onContextMenuTab(e, tab.path)}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '8px',
                padding: '0 12px',
                height: '100%',
                cursor: 'pointer',
                fontSize: '12px',
                fontFamily: 'var(--font-ui)',
                color: active ? 'var(--text-primary)' : 'var(--text-tertiary)',
                backgroundColor: active ? 'var(--bg-secondary)' : 'transparent',
                borderRight: '1px solid var(--border-subtle)',
                borderBottom: active ? '2px solid var(--border-strong)' : '2px solid transparent',
                whiteSpace: 'nowrap',
                flexShrink: 0,
                transition: 'all var(--transition-fast)',
                position: 'relative',
              }}
              onMouseEnter={(e) => {
                if (!active) {
                  e.currentTarget.style.color = 'var(--text-secondary)'
                  e.currentTarget.style.backgroundColor = 'var(--bg-tertiary)'
                }
              }}
              onMouseLeave={(e) => {
                if (!active) {
                  e.currentTarget.style.color = 'var(--text-tertiary)'
                  e.currentTarget.style.backgroundColor = 'transparent'
                }
              }}
            >
              <span style={{ fontWeight: active ? 500 : 400 }} title={tab.path}>{displayName(tab)}</span>
              {tab.dirty && (
                <span
                  title="未保存"
                  style={{
                    width: '6px',
                    height: '6px',
                    borderRadius: '50%',
                    background: 'var(--text-primary)',
                    flexShrink: 0,
                  }}
                />
              )}
              <button
                onClick={(e) => {
                  e.stopPropagation()
                  onClose(tab.path)
                }}
                title="关闭"
                style={{
                  border: 'none',
                  background: 'transparent',
                  color: 'var(--text-tertiary)',
                  cursor: 'pointer',
                  fontSize: '14px',
                  padding: '0',
                  lineHeight: 1,
                  width: '16px',
                  height: '16px',
                  borderRadius: '3px',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  transition: 'all var(--transition-fast)',
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.background = 'var(--bg-elevated)'
                  e.currentTarget.style.color = 'var(--text-primary)'
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.background = 'transparent'
                  e.currentTarget.style.color = 'var(--text-tertiary)'
                }}
              >
                ×
              </button>
            </div>
          )
        })}
      </div>
      {/* 关闭全部：仅在有打开标签时显示，固定在滚动区之外 */}
      {tabs.length > 0 && (
        <button
          onClick={onCloseAll}
          title="关闭全部标签页"
          style={{
            border: 'none',
            background: 'transparent',
            color: 'var(--text-tertiary)',
            cursor: 'pointer',
            padding: '0 8px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            flexShrink: 0,
            transition: 'all var(--transition-fast)',
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.background = 'var(--bg-elevated)'
            e.currentTarget.style.color = 'var(--text-primary)'
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.background = 'transparent'
            e.currentTarget.style.color = 'var(--text-tertiary)'
          }}
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <path d="M7 7l4 4M11 7L7 11" />
            <path d="M13 13l4 4M17 13l-4 4" />
          </svg>
        </button>
      )}
    </div>
  )
}

export default Tabs
