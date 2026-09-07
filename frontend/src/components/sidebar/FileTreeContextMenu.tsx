import { useEffect, useRef } from 'react'

// 文件树节点右键菜单 props：坐标 + 关闭 + 四个动作
interface FileTreeContextMenuProps {
  x: number
  y: number
  onClose: () => void
  onReveal: () => void
  onCopyAbsolute: () => void
  onCopyRelative: () => void
  onAddToChat: () => void
}

// 文件树节点右键菜单：固定定位浮层，点击外部 / Esc 收起。
// 样式对齐标签页右键菜单（TabContextMenu），复制类动作后菜单自动收起
function FileTreeContextMenu({
  x,
  y,
  onClose,
  onReveal,
  onCopyAbsolute,
  onCopyRelative,
  onAddToChat,
}: FileTreeContextMenuProps) {
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

  // 分隔线把「定位/复制」与「添加到对话」分成两组
  const items: { label: string; action: () => void; divider?: boolean }[] = [
    { label: '在资源管理器中打开', action: onReveal },
    { label: '复制绝对路径', action: onCopyAbsolute },
    { label: '复制相对路径', action: onCopyRelative },
    { label: '添加到对话', action: onAddToChat, divider: true },
  ]

  // 贴边兜底：菜单靠右/下溢出时收回到窗口内
  const left = Math.min(x, window.innerWidth - 190)
  const top = Math.min(y, window.innerHeight - 160)

  return (
    <div
      ref={ref}
      onContextMenu={(e) => e.preventDefault()}
      style={{
        position: 'fixed',
        left,
        top,
        zIndex: 200,
        minWidth: '170px',
        backgroundColor: 'var(--bg-elevated)',
        border: '1px solid var(--border)',
        borderRadius: 'var(--radius-md)',
        boxShadow: 'var(--shadow-md)',
        padding: '4px',
        display: 'flex',
        flexDirection: 'column',
      }}
    >
      {items.map(({ label, action, divider }) => (
        <div key={label}>
          {divider && (
            <div style={{ height: '1px', backgroundColor: 'var(--border-subtle)', margin: '4px 2px' }} />
          )}
          <button
            onClick={(e) => {
              e.stopPropagation()
              onClose()
              action()
            }}
            style={{
              border: 'none',
              background: 'transparent',
              color: 'var(--text-primary)',
              cursor: 'pointer',
              textAlign: 'left',
              width: '100%',
              padding: '5px 10px',
              fontSize: '12px',
              fontFamily: 'var(--font-ui)',
              borderRadius: 'var(--radius-sm)',
              transition: 'all var(--transition-fast)',
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.background = 'var(--bg-tertiary)'
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.background = 'transparent'
            }}
          >
            {label}
          </button>
        </div>
      ))}
    </div>
  )
}

export default FileTreeContextMenu
