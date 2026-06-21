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

// 标签页栏：可水平滚动，中键关闭，当前激活标签蓝色底边高亮
function Tabs({ tabs, activePath, onSwitch, onClose }: TabsProps) {
  return (
    <div
      style={{
        display: 'flex',
        backgroundColor: 'var(--bg-secondary)',
        borderBottom: '1px solid var(--border)',
        overflowX: 'auto',
        height: '36px',
        flexShrink: 0,
      }}
    >
      {tabs.map((tab) => {
        const active = tab.path === activePath
        return (
          <div
            key={tab.path}
            onClick={() => onSwitch(tab.path)}
            onMouseDown={(e) => {
              // 中键关闭标签
              if (e.button === 1) {
                e.preventDefault()
                onClose(tab.path)
              }
            }}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              padding: '0 10px',
              height: '100%',
              cursor: 'pointer',
              fontSize: '13px',
              color: active ? 'var(--text-primary)' : 'var(--text-secondary)',
              backgroundColor: active ? 'var(--bg-primary)' : 'transparent',
              borderBottom: active ? '2px solid var(--accent)' : '2px solid transparent',
              whiteSpace: 'nowrap',
              flexShrink: 0,
            }}
          >
            <span>{tab.name}</span>
            <button
              onClick={(e) => {
                e.stopPropagation()
                onClose(tab.path)
              }}
              title="关闭"
              style={{
                border: 'none',
                background: 'transparent',
                color: 'var(--text-secondary)',
                cursor: 'pointer',
                fontSize: '14px',
                padding: '0 2px',
                lineHeight: 1,
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
