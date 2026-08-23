import { useEffect, useRef } from 'react'

// 标签右键菜单 props：坐标 + 可用性判定 + 四项关闭动作
interface TabContextMenuProps {
  x: number
  y: number
  // 打开的标签总数（仅 1 个时「关闭其他」无意义）
  tabsCount: number
  // 锚点是否为最右标签（是则「关闭右侧」无意义）
  anchorIsLast: boolean
  onClose: () => void
  onCloseTab: () => void
  onCloseOthers: () => void
  onCloseRight: () => void
  onCloseAll: () => void
}

// 标签右键菜单：固定定位浮层，点击外部 / Esc 收起，空范围项置灰禁用
function TabContextMenu({
  x,
  y,
  tabsCount,
  anchorIsLast,
  onClose,
  onCloseTab,
  onCloseOthers,
  onCloseRight,
  onCloseAll,
}: TabContextMenuProps) {
  const ref = useRef<HTMLDivElement>(null)

  // 点击菜单外部或按 Esc 收起（mousedown 捕获，避免先触发下层点击）
  useEffect(() => {
    const onDown = (e: globalThis.MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose()
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('mousedown', onDown)
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('mousedown', onDown)
      window.removeEventListener('keydown', onKey)
    }
  }, [onClose])

  const items: { label: string; disabled: boolean; action: () => void }[] = [
    { label: '关闭', disabled: false, action: onCloseTab },
    { label: '关闭其他', disabled: tabsCount <= 1, action: onCloseOthers },
    { label: '关闭右侧', disabled: anchorIsLast, action: onCloseRight },
    { label: '关闭全部', disabled: false, action: onCloseAll },
  ]

  // 贴边兜底：菜单靠右/下溢出时收回到窗口内
  const left = Math.min(x, window.innerWidth - 170)
  const top = Math.min(y, window.innerHeight - 150)

  return (
    <div
      ref={ref}
      onContextMenu={(e) => e.preventDefault()}
      style={{
        position: 'fixed',
        left,
        top,
        zIndex: 200,
        minWidth: '150px',
        backgroundColor: 'var(--bg-elevated)',
        border: '1px solid var(--border)',
        borderRadius: 'var(--radius-md)',
        boxShadow: 'var(--shadow-md)',
        padding: '4px',
        display: 'flex',
        flexDirection: 'column',
      }}
    >
      {items.map(({ label, disabled, action }) => (
        <button
          key={label}
          disabled={disabled}
          onClick={(e) => {
            e.stopPropagation()
            onClose()
            action()
          }}
          style={{
            border: 'none',
            background: 'transparent',
            color: disabled ? 'var(--text-tertiary)' : 'var(--text-primary)',
            cursor: disabled ? 'default' : 'pointer',
            opacity: disabled ? 0.45 : 1,
            textAlign: 'left',
            padding: '5px 10px',
            fontSize: '12px',
            fontFamily: 'var(--font-ui)',
            borderRadius: 'var(--radius-sm)',
            transition: 'all var(--transition-fast)',
          }}
          onMouseEnter={(e) => {
            if (!disabled) e.currentTarget.style.background = 'var(--bg-tertiary)'
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.background = 'transparent'
          }}
        >
          {label}
        </button>
      ))}
    </div>
  )
}

export default TabContextMenu
