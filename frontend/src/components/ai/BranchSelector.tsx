import { useState, useRef, useEffect } from 'react'

interface BranchSelectorProps {
  currentBranch: string
  branches: string[]
  onCheckout: (branch: string) => void
}

function BranchSelector({ currentBranch, branches, onCheckout }: BranchSelectorProps) {
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

  // 没有分支（非 Git 仓库）时不显示
  if (branches.length === 0 && !currentBranch) return null

  return (
    <div ref={menuRef} style={{ position: 'relative' }}>
      <button
        onClick={() => setOpen(!open)}
        title="切换分支"
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
          fontFamily: 'var(--font-mono)',
          fontWeight: 500,
          cursor: 'pointer',
          transition: 'all var(--transition-fast)',
          whiteSpace: 'nowrap',
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
        {/* git-branch 图标 */}
        <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0 }}>
          <circle cx="6" cy="6" r="2.5" />
          <circle cx="6" cy="18" r="2.5" />
          <circle cx="18" cy="12" r="2.5" />
          <path d="M6 8.5v7" />
          <path d="M6 12h7a3 3 0 0 0 2.5-2.5" />
        </svg>
        <span>{currentBranch || '无分支'}</span>
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
            minWidth: '200px',
            maxHeight: '280px',
            overflowY: 'auto',
          }}
        >
          {branches.map((branch) => {
            const isActive = branch === currentBranch
            return (
              <div
                key={branch}
                onClick={() => {
                  if (!isActive) onCheckout(branch)
                  setOpen(false)
                }}
                style={{
                  padding: '7px 10px',
                  cursor: 'pointer',
                  backgroundColor: isActive ? 'var(--selected-bg)' : 'transparent',
                  transition: 'background var(--transition-fast)',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                }}
                onMouseEnter={(e) => {
                  if (!isActive) e.currentTarget.style.backgroundColor = 'var(--hover-bg)'
                }}
                onMouseLeave={(e) => {
                  if (!isActive) e.currentTarget.style.backgroundColor = 'transparent'
                }}
              >
                <span style={{
                  color: 'var(--text-primary)',
                  fontSize: '12px',
                  fontFamily: 'var(--font-mono)',
                  fontWeight: 500,
                }}>
                  {branch}
                </span>
                {isActive && (
                  <span style={{ color: 'var(--text-primary)', fontSize: '11px' }}>✓</span>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

export default BranchSelector
