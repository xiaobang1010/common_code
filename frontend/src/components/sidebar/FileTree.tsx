import { useCallback, useEffect, useState } from 'react'
import { filesApi } from '../../api/client'

// 文件列表接口返回的单项
interface FileItem {
  name: string
  type: 'dir' | 'file'
  path: string
}

interface FileTreeProps {
  onFileOpen: (path: string) => void
  // 当前激活文件路径：树中对应节点高亮（编辑器树列使用，可不传）
  activePath?: string
  // 双击文件节点显式固定预览标签（打开后保留为正式标签）
  onPinFile?: (path: string) => void
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
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--text-primary)" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
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

// 过滤命中段高亮：名称中包含关键词的部分加深底色
function HighlightedName({ name, q }: { name: string; q: string }) {
  const idx = name.toLowerCase().indexOf(q)
  if (idx < 0) return <span>{name}</span>
  return (
    <span>
      {name.slice(0, idx)}
      <span style={{ backgroundColor: 'var(--selected-bg)', color: 'var(--text-primary)', fontWeight: 600 }}>{name.slice(idx, idx + q.length)}</span>
      {name.slice(idx + q.length)}
    </span>
  )
}

// 过滤模式下整树节点的完整结构（含全部子孙）
interface FullTreeNode {
  item: FileItem
  children: FullTreeNode[]
}

// 后端递归列表项转为前端树节点
const toFullNode = (it: { name: string; type: 'dir' | 'file'; path: string; children?: unknown[] }): FullTreeNode => ({
  item: { name: it.name, type: it.type, path: it.path },
  children: (it.children || []).map((c) => toFullNode(c as { name: string; type: 'dir' | 'file'; path: string; children?: unknown[] })),
})

// 一次性取整棵树：过滤激活时需要全局视角才能保留匹配项的父级目录链
const loadFullTree = async (): Promise<FullTreeNode[]> => {
  const res = await fetch('/api/files/list?path=.&recursive=true')
  const data = await res.json()
  return (data.items || []).map((it: { name: string; type: 'dir' | 'file'; path: string; children?: unknown[] }) => toFullNode(it))
}

// 剪枝：只保留名称命中的节点及其父级目录链
const pruneTree = (nodes: FullTreeNode[], q: string): FullTreeNode[] =>
  nodes
    .map((n) => ({ ...n, children: pruneTree(n.children, q) }))
    .filter((n) => n.item.name.toLowerCase().includes(q) || n.children.length > 0)

// 单个树节点
interface FileTreeNodeProps {
  item: FileItem
  depth: number
  onFileOpen: (path: string) => void
  activePath?: string
  onPinFile?: (path: string) => void
}

function FileTreeNode({ item, depth, onFileOpen, activePath, onPinFile }: FileTreeNodeProps) {
  const [expanded, setExpanded] = useState(false)
  const [children, setChildren] = useState<FileItem[]>([])
  const [loaded, setLoaded] = useState(false)
  const [loading, setLoading] = useState(false)
  const [hovered, setHovered] = useState(false)

  const isDir = item.type === 'dir'
  // 当前打开文件的节点高亮，选中与视线在树内闭环
  const isActive = !isDir && activePath === item.path

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
        onDoubleClick={() => {
          if (!isDir) onPinFile?.(item.path)
        }}
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
          backgroundColor: isActive ? 'var(--selected-bg)' : hovered ? 'var(--hover-bg)' : 'transparent',
          borderRadius: 'var(--radius-sm)',
          margin: '0 4px',
          transition: 'background var(--transition-fast)',
        }}
      >
        <span style={{ width: '14px', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          {loading ? <LoadingIcon /> : isDir ? <FolderIcon open={expanded} /> : <FileIcon color={fileColor} />}
        </span>
        <span style={{ fontWeight: isDir ? 500 : isActive ? 500 : 400, color: isActive ? 'var(--text-primary)' : undefined }}>{item.name}</span>
      </div>
      {isDir && expanded && loaded && (
        <div>
          {children.map((child) => (
            <FileTreeNode key={child.path} item={child} depth={depth + 1} onFileOpen={onFileOpen} activePath={activePath} onPinFile={onPinFile} />
          ))}
        </div>
      )}
    </div>
  )
}

// 过滤结果树节点：全部展开、命中高亮，点击文件打开
function FilteredTreeNode({ node, depth, q, onFileOpen, activePath, onPinFile }: {
  node: FullTreeNode
  depth: number
  q: string
  onFileOpen: (path: string) => void
  activePath?: string
  onPinFile?: (path: string) => void
}) {
  const [hovered, setHovered] = useState(false)
  const isDir = node.item.type === 'dir'
  const isActive = !isDir && activePath === node.item.path
  const fileColor = getFileColor(node.item.name)
  return (
    <div>
      <div
        onClick={() => {
          if (!isDir) onFileOpen(node.item.path)
        }}
        onDoubleClick={() => {
          if (!isDir) onPinFile?.(node.item.path)
        }}
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '6px',
          paddingLeft: depth * 14 + 8,
          paddingRight: '8px',
          height: '26px',
          cursor: isDir ? 'default' : 'pointer',
          color: isDir || isActive ? 'var(--text-primary)' : fileColor,
          fontSize: '13px',
          fontFamily: 'var(--font-ui)',
          whiteSpace: 'nowrap',
          userSelect: 'none',
          backgroundColor: isActive ? 'var(--selected-bg)' : hovered ? 'var(--hover-bg)' : 'transparent',
          borderRadius: 'var(--radius-sm)',
          margin: '0 4px',
          transition: 'background var(--transition-fast)',
        }}
      >
        <span style={{ width: '14px', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          {isDir ? <FolderIcon open={true} /> : <FileIcon color={isActive ? 'var(--text-primary)' : fileColor} />}
        </span>
        <span style={{ fontWeight: isDir ? 500 : isActive ? 500 : 400 }}>
          <HighlightedName name={node.item.name} q={q} />
        </span>
      </div>
      {node.children.map((c) => (
        <FilteredTreeNode key={c.item.path} node={c} depth={depth + 1} q={q} onFileOpen={onFileOpen} activePath={activePath} onPinFile={onPinFile} />
      ))}
    </div>
  )
}

function FileTree({ onFileOpen, activePath, onPinFile }: FileTreeProps) {
  const [rootItems, setRootItems] = useState<FileItem[]>([])
  // 首开加载：无数据时的全量 loading；刷新期间用 refreshing 轻量指示，不清空旧树
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState('')
  const [treeVersion, setTreeVersion] = useState(0)
  const [creating, setCreating] = useState<'file' | 'dir' | null>(null)
  const [createValue, setCreateValue] = useState('')
  const [createError, setCreateError] = useState('')

  // 搜索名称过滤：激活时加载整棵树做全局过滤，保留父级目录链
  const [filter, setFilter] = useState('')
  const [filterTree, setFilterTree] = useState<FullTreeNode[] | null>(null)
  const [filterLoading, setFilterLoading] = useState(false)

  useEffect(() => {
    const q = filter.trim().toLowerCase()
    if (!q) {
      setFilterTree(null)
      setFilterLoading(false)
      return
    }
    let cancelled = false
    setFilterLoading(true)
    loadFullTree()
      .then((tree) => {
        if (!cancelled) {
          setFilterTree(pruneTree(tree, q))
          setFilterLoading(false)
        }
      })
      .catch(() => {
        if (!cancelled) setFilterLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [filter])

  const loadRoot = useCallback(async () => {
    setRefreshing(true)
    try {
      const res = await fetch('/api/files/list?path=.')
      const data = await res.json()
      setRootItems(data.items || [])
      setError('')
    } catch (e) {
      // 刷新失败保留上次数据，仅记录错误供轻量提示；首开失败才显示全量错误块
      setError('加载失败')
      console.error(e)
    } finally {
      setLoading(false)
      setRefreshing(false)
    }
  }, [])

  useEffect(() => {
    loadRoot()
  }, [loadRoot])

  // 订阅文件变更事件：AI 写盘后刷新文件树；断线重连时也刷新一次兜底
  useEffect(() => {
    const es = new EventSource('/api/files/events')
    const refresh = () => void loadRoot()
    es.onopen = refresh
    es.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data)
        if (data.type === 'file_changed') refresh()
      } catch {
        // 忽略无法解析的事件
      }
    }
    return () => es.close()
  }, [loadRoot])

  // 新建文件/目录：弹出内联输入框
  const startCreate = (type: 'file' | 'dir') => {
    setCreating(type)
    setCreateValue('')
    setCreateError('')
  }

  const confirmCreate = async () => {
    const path = createValue.trim()
    const type = creating
    if (!path || !type) return
    try {
      await filesApi.create(path, type)
      setCreating(null)
      setCreateValue('')
      // 重建文件树（重置展开态但保证看到新文件）
      setTreeVersion((v) => v + 1)
      await loadRoot()
      if (type === 'file') {
        onFileOpen(path)
      }
    } catch (e) {
      setCreateError((e as Error).message || '创建失败')
    }
  }

  // 首开（尚无数据）才显示全量加载；刷新期间保留旧树
  if (loading && rootItems.length === 0) {
    return (
      <div style={{ padding: '16px', color: 'var(--text-tertiary)', fontSize: '12px', display: 'flex', alignItems: 'center', gap: '8px' }}>
        <LoadingIcon />
        加载中
      </div>
    )
  }
  // 首开失败（无数据可兜底）显示全量错误 + 重试
  if (error && rootItems.length === 0) {
    return (
      <div style={{ padding: '16px', color: 'var(--error)', fontSize: '12px', display: 'flex', alignItems: 'center', gap: '8px' }}>
        {error}
        <button
          onClick={() => void loadRoot()}
          style={{ cursor: 'pointer', background: 'transparent', border: '1px solid var(--border)', color: 'var(--text-secondary)', borderRadius: 'var(--radius-sm)', fontSize: '12px', padding: '2px 8px' }}
        >
          重试
        </button>
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      {/* 搜索名称过滤框：树内过滤，保留父级目录结构，匹配文件名高亮 */}
      <div style={{ padding: '8px 8px 0' }}>
        <input
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="搜索名称"
          style={{
            width: '100%',
            boxSizing: 'border-box',
            border: '1px solid var(--border)',
            background: 'var(--bg-elevated)',
            color: 'var(--text-primary)',
            fontSize: '12px',
            fontFamily: 'var(--font-ui)',
            padding: '5px 8px',
            borderRadius: 'var(--radius-sm)',
            outline: 'none',
          }}
        />
      </div>

      {/* 顶部新建入口 */}
      <div
        style={{
          display: 'flex',
          gap: '6px',
          padding: '8px',
          borderBottom: '1px solid var(--border-subtle)',
        }}
      >
        <button
          onClick={() => startCreate('file')}
          title="新建文件"
          style={{
            flex: 1,
            border: '1px solid var(--border)',
            background: 'transparent',
            color: 'var(--text-secondary)',
            cursor: 'pointer',
            fontSize: '12px',
            fontFamily: 'var(--font-ui)',
            padding: '4px 0',
            borderRadius: 'var(--radius-sm)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            gap: '4px',
          }}
        >
          <FileIcon color="var(--text-secondary)" />
          新建文件
        </button>
        <button
          onClick={() => startCreate('dir')}
          title="新建目录"
          style={{
            flex: 1,
            border: '1px solid var(--border)',
            background: 'transparent',
            color: 'var(--text-secondary)',
            cursor: 'pointer',
            fontSize: '12px',
            fontFamily: 'var(--font-ui)',
            padding: '4px 0',
            borderRadius: 'var(--radius-sm)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            gap: '4px',
          }}
        >
          <FolderIcon open={false} />
          新建目录
        </button>
      </div>

      {/* 新建输入框 */}
      {creating && (
        <div style={{ padding: '0 8px 8px', display: 'flex', flexDirection: 'column', gap: '6px' }}>
          <input
            autoFocus
            value={createValue}
            onChange={(e) => setCreateValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') void confirmCreate()
              if (e.key === 'Escape') setCreating(null)
            }}
            placeholder={creating === 'file' ? '相对路径，如 src/foo.py' : '相对路径，如 src/utils'}
            style={{
              border: '1px solid var(--border)',
              background: 'var(--bg-elevated)',
              color: 'var(--text-primary)',
              fontSize: '12px',
              fontFamily: 'var(--font-ui)',
              padding: '5px 8px',
              borderRadius: 'var(--radius-sm)',
              outline: 'none',
            }}
          />
          <div style={{ display: 'flex', gap: '6px' }}>
            <button
              onClick={() => void confirmCreate()}
              style={{ padding: '3px 10px', cursor: 'pointer', background: 'var(--button-primary-bg)', color: 'var(--button-primary-text)', border: 'none', borderRadius: 'var(--radius-sm)', fontSize: '12px' }}
            >
              确定
            </button>
            <button
              onClick={() => setCreating(null)}
              style={{ padding: '3px 10px', cursor: 'pointer', background: 'transparent', color: 'var(--text-secondary)', border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)', fontSize: '12px' }}
            >
              取消
            </button>
          </div>
          {createError && <div style={{ color: 'var(--error)', fontSize: '12px' }}>{createError}</div>}
        </div>
      )}

      {/* 轻量状态条：刷新中转圈、刷新失败提示 + 重试，均不清空旧树 */}
      {(refreshing || error) && rootItems.length > 0 && (
        <div
          style={{
            padding: '3px 8px',
            fontSize: '11px',
            color: 'var(--text-tertiary)',
            display: 'flex',
            alignItems: 'center',
            gap: '6px',
          }}
        >
          {refreshing ? (
            <>
              <LoadingIcon />
              刷新中
            </>
          ) : (
            <>
              <span style={{ color: 'var(--error)' }}>{error}，已保留上次结果</span>
              <button
                onClick={() => void loadRoot()}
                style={{ cursor: 'pointer', background: 'transparent', border: 'none', color: 'var(--text-secondary)', fontSize: '11px', padding: '0', textDecoration: 'underline' }}
              >
                重试
              </button>
            </>
          )}
        </div>
      )}

      {/* 文件树列表：过滤激活时展示全局过滤结果（懒加载树保持挂载，展开态不丢） */}
      <div key={treeVersion} style={{ flex: 1, overflow: 'auto', padding: '6px 0', display: filterTree !== null ? 'none' : 'block' }}>
        {rootItems.map((item) => (
          <FileTreeNode key={item.path} item={item} depth={0} onFileOpen={onFileOpen} activePath={activePath} onPinFile={onPinFile} />
        ))}
      </div>
      {filterTree !== null && (
        <div style={{ flex: 1, overflow: 'auto', padding: '6px 0' }}>
          {filterLoading && filterTree.length === 0 ? (
            <div style={{ padding: '8px 16px', color: 'var(--text-tertiary)', fontSize: '12px' }}>过滤中…</div>
          ) : filterTree.length === 0 ? (
            <div style={{ padding: '8px 16px', color: 'var(--text-tertiary)', fontSize: '12px' }}>没有匹配的文件</div>
          ) : (
            filterTree.map((n) => (
              <FilteredTreeNode key={n.item.path} node={n} depth={0} q={filter.trim().toLowerCase()} onFileOpen={onFileOpen} activePath={activePath} onPinFile={onPinFile} />
            ))
          )}
        </div>
      )}
    </div>
  )
}

export default FileTree
