import { useEffect, useState } from 'react'

// 文件列表接口返回的单项
interface FileItem {
  name: string
  type: 'dir' | 'file'
  path: string
}

interface FileTreeProps {
  onFileOpen: (path: string) => void
}

// 根据文件扩展名返回对应颜色 - 精致的语法色
function getFileColor(name: string): string {
  if (name.endsWith('.py')) return 'var(--syntax-function)'
  if (name.endsWith('.js') || name.endsWith('.jsx')) return 'var(--warning)'
  if (name.endsWith('.ts') || name.endsWith('.tsx')) return 'var(--info)'
  if (name.endsWith('.json')) return 'var(--syntax-number)'
  if (name.endsWith('.md')) return 'var(--text-secondary)'
  if (name.endsWith('.css') || name.endsWith('.scss')) return 'var(--syntax-keyword)'
  if (name.endsWith('.html')) return 'var(--syntax-number)'
  return 'var(--text-primary)'
}

// 文件夹图标 SVG
function FolderIcon({ open }: { open: boolean }) {
  return open ? (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z" />
      <path d="M3 12h18" stroke="var(--border-strong)" strokeWidth="1" />
    </svg>
  ) : (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--text-secondary)" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z" />
    </svg>
  )
}

// 文件图标 SVG
function FileIcon({ color }: { color: string }) {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
      <path d="M14 2v6h6" />
    </svg>
  )
}

// 加载中图标
function LoadingIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="var(--text-tertiary)" strokeWidth="2" strokeLinecap="round">
      <path d="M21 12a9 9 0 1 1-6.219-8.56" style={{ animation: 'spin 1s linear infinite' }} />
    </svg>
  )
}

// 单个树节点
interface FileTreeNodeProps {
  item: FileItem
  depth: number
  onFileOpen: (path: string) => void
}

function FileTreeNode({ item, depth, onFileOpen }: FileTreeNodeProps) {
  const [expanded, setExpanded] = useState(false)
  const [children, setChildren] = useState<FileItem[]>([])
  const [loaded, setLoaded] = useState(false)
  const [loading, setLoading] = useState(false)
  const [hovered, setHovered] = useState(false)

  const isDir = item.type === 'dir'

  const handleClick = async () => {
    if (!isDir) {
      onFileOpen(item.path)
      return
    }
    if (!expanded && !loaded) {
      setLoading(true)
      try {
        const res = await fetch(`/api/files/list?path=${encodeURIComponent(item.path)}`)
        const data = await res.json()
        setChildren(data.items || [])
        setLoaded(true)
      } catch (e) {
        console.error('加载目录失败', e)
      } finally {
        setLoading(false)
      }
    }
    setExpanded(!expanded)
  }

  const fileColor = getFileColor(item.name)

  return (
    <div>
      <div
        onClick={handleClick}
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '6px',
          paddingLeft: depth * 14 + 8,
          paddingRight: '8px',
          height: '26px',
          cursor: 'pointer',
          color: isDir ? 'var(--text-primary)' : fileColor,
          fontSize: '13px',
          fontFamily: 'var(--font-ui)',
          whiteSpace: 'nowrap',
          userSelect: 'none',
          backgroundColor: hovered ? 'var(--bg-tertiary)' : 'transparent',
          borderRadius: 'var(--radius-sm)',
          margin: '0 4px',
          transition: 'background var(--transition-fast)',
        }}
      >
        <span style={{ width: '14px', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          {loading ? <LoadingIcon /> : isDir ? <FolderIcon open={expanded} /> : <FileIcon color={fileColor} />}
        </span>
        <span style={{ fontWeight: isDir ? 500 : 400 }}>{item.name}</span>
      </div>
      {isDir && expanded && loaded && (
        <div>
          {children.map((child) => (
            <FileTreeNode key={child.path} item={child} depth={depth + 1} onFileOpen={onFileOpen} />
          ))}
        </div>
      )}
    </div>
  )
}

function FileTree({ onFileOpen }: FileTreeProps) {
  const [rootItems, setRootItems] = useState<FileItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    const loadRoot = async () => {
      try {
        const res = await fetch('/api/files/list?path=.')
        const data = await res.json()
        setRootItems(data.items || [])
      } catch (e) {
        setError('加载失败')
        console.error(e)
      } finally {
        setLoading(false)
      }
    }
    loadRoot()
  }, [])

  if (loading) {
    return (
      <div style={{ padding: '16px', color: 'var(--text-tertiary)', fontSize: '12px', display: 'flex', alignItems: 'center', gap: '8px' }}>
        <LoadingIcon />
        加载中
      </div>
    )
  }
  if (error) {
    return (
      <div style={{ padding: '16px', color: 'var(--error)', fontSize: '12px' }}>{error}</div>
    )
  }

  return (
    <div style={{ flex: 1, overflow: 'auto', padding: '6px 0' }}>
      {rootItems.map((item) => (
        <FileTreeNode key={item.path} item={item} depth={0} onFileOpen={onFileOpen} />
      ))}
    </div>
  )
}

export default FileTree
