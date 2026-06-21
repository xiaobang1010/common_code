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

// 根据文件扩展名返回对应颜色
function getFileColor(name: string): string {
  if (name.endsWith('.py')) return 'var(--accent)' // 蓝色
  if (name.endsWith('.js') || name.endsWith('.ts')) return 'var(--warning)' // 黄色
  if (name.endsWith('.json')) return 'var(--text-secondary)' // 灰色
  if (name.endsWith('.md')) return 'var(--text-primary)' // 白色
  return 'var(--text-primary)'
}

// 单个树节点，递归渲染子项
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

  const isDir = item.type === 'dir'

  // 点击文件夹：展开/折叠，首次展开时懒加载子目录；点击文件：触发打开
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

  const icon = isDir ? (expanded ? '📂' : '📁') : '📄'

  return (
    <div>
      <div
        onClick={handleClick}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '4px',
          paddingLeft: depth * 12 + 8,
          paddingRight: '8px',
          height: '24px',
          cursor: 'pointer',
          color: isDir ? 'var(--text-primary)' : getFileColor(item.name),
          fontSize: '13px',
          whiteSpace: 'nowrap',
          userSelect: 'none',
        }}
      >
        <span style={{ width: '16px', textAlign: 'center' }}>
          {loading ? '⏳' : icon}
        </span>
        <span>{item.name}</span>
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

  // 初始加载根目录
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
      <div style={{ padding: '8px', color: 'var(--text-secondary)', fontSize: '13px' }}>
        加载中...
      </div>
    )
  }
  if (error) {
    return (
      <div style={{ padding: '8px', color: 'var(--error)', fontSize: '13px' }}>{error}</div>
    )
  }

  return (
    <div style={{ flex: 1, overflow: 'auto', padding: '4px 0' }}>
      {rootItems.map((item) => (
        <FileTreeNode key={item.path} item={item} depth={0} onFileOpen={onFileOpen} />
      ))}
    </div>
  )
}

export default FileTree
