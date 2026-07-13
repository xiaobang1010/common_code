import { useState, useRef, useEffect } from 'react'
import type { WorkspaceInfo } from '../../api/client'

interface WorkspaceSelectorProps {
  currentWorkspace: WorkspaceInfo | null
  workspaces: WorkspaceInfo[]
  onSwitch: (path: string) => void
  onBrowse: () => void
}

// 从路径提取最后一段作为显示名
function basename(path: string): string {
  const parts = path.replace(/\\/g, '/').split('/').filter(Boolean)
  return parts[parts.length - 1] || path
}

function WorkspaceSelector({ currentWorkspace, workspaces, onSwitch, onBrowse }: WorkspaceSelectorProps) {
  const [open, setOpen] = useState(false)
  const menuRef = useRef<HTMLDivElement>(null)

  // 点击外部关闭下拉菜单
  useEffect(() => {
    if (!open) return
    const handleClick = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('click', handleClick)
    return () => document.removeEventListener('click', handleClick)
  }, [open])

  const displayName = currentWorkspace ? basename(currentWorkspace.path) : '未选择工作区'

  return (
    <div ref={menuRef} style={{ position: 'relative' }}>
      <button
        onClick={() => setOpen(!open)}
        title={currentWorkspace?.path || '未选择工作区'}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '4px',
          padding: '3px 8px',
          border: '1px solid var(--border)',
          borderRadius: 'var(--radius-sm)',
          background: 'transparent',
          color: 'var(--text-secondary)',
          fontSize: '11px',
          fontFamily: 'var(--font-ui)',
          fontWeight: 500,
          cursor: 'pointer',
          transition: 'all var(--transition-fast)',
          whiteSpace: 'nowrap',
          maxWidth: '180px',
          overflow: 'hidden',
          textOverflow: 'ellipsis',
        }}
        onMouseEnter={(e) => {
          e.currentTarget.style.borderColor = 'var(--border-strong)'
          e.currentTarget.style.color = 'var(--text-primary)'
        }}
        onMouseLeave={(e) => {
          e.currentTarget.style.borderColor = 'var(--border)'
          e.currentTarget.style.color = 'var(--text-secondary)'
        }}
      >
        {/* 文件夹图标 */}
        <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0 }}>
          <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z" />
        </svg>
        <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>{displayName}</span>
        <svg width="8" height="8" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0 }}>
          <path d="m6 9 6 6 6-6" />
        </svg>
      </button>

      {open && (
        <div
          style={{
            position: 'absolute',
            top: '100%',
            left: 0,
            marginTop: '4px',
            backgroundColor: 'var(--bg-elevated)',
            border: '1px solid var(--border-strong)',
            borderRadius: 'var(--radius-md)',
            boxShadow: 'var(--shadow-md)',
            backdropFilter: 'blur(8px)',
            zIndex: 30,
            minWidth: '240px',
            maxHeight: '300px',
            overflowY: 'auto',
          }}
        >
          {/* 已知工作区列表 */}
          {workspaces.map((ws) => {
            const isActive = currentWorkspace?.path === ws.path
            return (
              <div
                key={ws.path}
                onClick={() => {
                  onSwitch(ws.path)
                  setOpen(false)
                }}
                style={{
                  padding: '8px 10px',
                  cursor: 'pointer',
                  backgroundColor: isActive ? 'var(--accent-soft)' : 'transparent',
                  transition: 'background var(--transition-fast)',
                }}
                onMouseEnter={(e) => {
                  if (!isActive) e.currentTarget.style.backgroundColor = 'var(--bg-tertiary)'
                }}
                onMouseLeave={(e) => {
                  if (!isActive) e.currentTarget.style.backgroundColor = 'transparent'
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                  <span style={{
                    color: isActive ? 'var(--accent)' : 'var(--text-primary)',
                    fontSize: '12px',
                    fontFamily: 'var(--font-ui)',
                    fontWeight: 500,
                  }}>
                    {ws.name || basename(ws.path)}
                  </span>
                  {isActive && (
                    <span style={{ color: 'var(--accent)', fontSize: '11px' }}>✓</span>
                  )}
                </div>
                <div style={{
                  color: 'var(--text-tertiary)',
                  fontSize: '10px',
                  marginTop: '2px',
                  fontFamily: 'var(--font-mono)',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                }}>
                  {ws.path}
                </div>
              </div>
            )
          })}

          {/* 分隔线 */}
          {workspaces.length > 0 && (
            <div style={{ height: '1px', backgroundColor: 'var(--border-subtle)' }} />
          )}

          {/* 浏览... 选项 */}
          <div
            onClick={() => {
              onBrowse()
              setOpen(false)
            }}
            style={{
              padding: '8px 10px',
              cursor: 'pointer',
              color: 'var(--text-secondary)',
              fontSize: '12px',
              fontFamily: 'var(--font-ui)',
              transition: 'background var(--transition-fast)',
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.backgroundColor = 'var(--bg-tertiary)'
              e.currentTarget.style.color = 'var(--text-primary)'
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.backgroundColor = 'transparent'
              e.currentTarget.style.color = 'var(--text-secondary)'
            }}
          >
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
              <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z" />
              <path d="M12 11v4M10 13h4" />
            </svg>
            浏览...
          </div>
        </div>
      )}
    </div>
  )
}

export default WorkspaceSelector
