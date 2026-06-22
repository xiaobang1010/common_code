interface Tab {
  path: string
  name: string
}

interface TabsProps {
  tabs: Tab[]
  activePath: string
  onSwitch: (path: string) => void
  onClose: (path: string) => void
}

// 标签页栏：可水平滚动，中键关闭，当前激活标签底部高亮
function Tabs({ tabs, activePath, onSwitch, onClose }: TabsProps) {
  return (
    <div
      style={{
        display: 'flex',
        backgroundColor: 'var(--bg-primary)',
        overflowX: 'auto',
        height: '38px',
        flexShrink: 0,
        borderBottom: '1px solid var(--border)',
      }}
    >
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
              borderBottom: active ? '2px solid var(--accent)' : '2px solid transparent',
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
            <span style={{ fontWeight: active ? 500 : 400 }}>{tab.name}</span>
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
  )
}

export default Tabs
