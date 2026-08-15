import { useMemo, useState } from 'react'
import { useGitStatus, dedupeChanges, type GitChange } from '../useGitStatus'

interface ReviewCardProps {
  // 点击变更文件在编辑器打开
  onFileOpen: (path: string) => void
}

// 目录树节点：子目录 + 该目录下的变更文件
interface DirNode {
  dirs: Map<string, DirNode>
  files: GitChange[]
}

// 按路径把变更文件组织成目录树
function buildTree(changes: GitChange[]): DirNode {
  const root: DirNode = { dirs: new Map(), files: [] }
  for (const change of changes) {
    // git 输出的路径统一是正斜杠
    const segments = change.path.split('/')
    let node = root
    for (let i = 0; i < segments.length - 1; i++) {
      const name = segments[i]
      if (!node.dirs.has(name)) {
        node.dirs.set(name, { dirs: new Map(), files: [] })
      }
      node = node.dirs.get(name)!
    }
    node.files.push(change)
  }
  return root
}

// 变更状态 → 状态字母
function statusLetter(status: string): string {
  if (status === 'added') return 'A'
  if (status === 'modified') return 'M'
  if (status === 'deleted') return 'D'
  return '?'
}

// 变更状态 → 颜色（新增/未跟踪绿、修改黄、删除红）
function statusColor(status: string): string {
  if (status === 'added') return 'var(--success)'
  if (status === 'modified') return 'var(--warning)'
  if (status === 'deleted') return 'var(--error)'
  return 'var(--text-secondary)'
}

// 变更文件行：文件名 + +N/-N 行数 + 状态字母
function FileRow({ change, depth, onFileOpen }: { change: GitChange; depth: number; onFileOpen: (path: string) => void }) {
  const [hovered, setHovered] = useState(false)
  const name = change.path.split('/').pop() || change.path

  return (
    <div
      onClick={() => onFileOpen(change.path)}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      title={change.path}
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: '6px',
        paddingLeft: depth * 14 + 8,
        paddingRight: '8px',
        height: '26px',
        cursor: 'pointer',
        fontSize: '12px',
        backgroundColor: hovered ? 'var(--bg-tertiary)' : 'transparent',
        borderRadius: 'var(--radius-sm)',
        margin: '0 4px',
        transition: 'background var(--transition-fast)',
      }}
    >
      <span
        style={{
          flex: 1,
          color: statusColor(change.status),
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          whiteSpace: 'nowrap',
        }}
      >
        {name}
      </span>
      {/* 行数统计：新增绿、删除红（为 0 时不显示） */}
      <span style={{ fontFamily: 'var(--font-mono)', fontSize: '11px', color: 'var(--success)', flexShrink: 0 }}>
        +{change.additions}
      </span>
      {change.deletions > 0 && (
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: '11px', color: 'var(--error)', flexShrink: 0 }}>
          -{change.deletions}
        </span>
      )}
      {/* 状态字母 */}
      <span
        style={{
          fontFamily: 'var(--font-mono)',
          fontSize: '11px',
          color: statusColor(change.status),
          width: '12px',
          textAlign: 'center',
          flexShrink: 0,
        }}
      >
        {statusLetter(change.status)}
      </span>
    </div>
  )
}

// 目录行 + 子树递归渲染，目录可折叠（默认展开）
function DirBranch({
  name,
  node,
  depth,
  pathPrefix,
  collapsedDirs,
  onToggleDir,
  onFileOpen,
}: {
  name: string
  node: DirNode
  depth: number
  pathPrefix: string
  collapsedDirs: Set<string>
  onToggleDir: (path: string) => void
  onFileOpen: (path: string) => void
}) {
  const fullPath = pathPrefix ? `${pathPrefix}/${name}` : name
  const collapsed = collapsedDirs.has(fullPath)
  const [hovered, setHovered] = useState(false)

  return (
    <div>
      <div
        onClick={() => onToggleDir(fullPath)}
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '5px',
          paddingLeft: depth * 14 + 8,
          paddingRight: '8px',
          height: '24px',
          cursor: 'pointer',
          fontSize: '12px',
          color: 'var(--text-secondary)',
          backgroundColor: hovered ? 'var(--bg-tertiary)' : 'transparent',
          borderRadius: 'var(--radius-sm)',
          margin: '0 4px',
          userSelect: 'none',
          transition: 'background var(--transition-fast)',
        }}
      >
        <svg
          width="10"
          height="10"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2.5"
          strokeLinecap="round"
          strokeLinejoin="round"
          style={{
            flexShrink: 0,
            transform: collapsed ? 'rotate(0deg)' : 'rotate(90deg)',
            transition: 'transform 0.15s ease',
          }}
        >
          <path d="M9 6l6 6-6 6" />
        </svg>
        <span style={{ fontWeight: 500 }}>{name}</span>
      </div>
      {!collapsed && (
        <div>
          {[...node.dirs.entries()].map(([childName, childNode]) => (
            <DirBranch
              key={childName}
              name={childName}
              node={childNode}
              depth={depth + 1}
              pathPrefix={fullPath}
              collapsedDirs={collapsedDirs}
              onToggleDir={onToggleDir}
              onFileOpen={onFileOpen}
            />
          ))}
          {node.files.map((f) => (
            <FileRow key={f.path} change={f} depth={depth + 1} onFileOpen={onFileOpen} />
          ))}
        </div>
      )}
    </div>
  )
}

// 审查卡：只列变动文件，顶部汇总 + 目录树形式的变更清单
function ReviewCard({ onFileOpen }: ReviewCardProps) {
  const gitStatus = useGitStatus()
  // 折叠的目录集合（默认全部展开）
  const [collapsedDirs, setCollapsedDirs] = useState<Set<string>>(new Set())

  const changes = useMemo(() => {
    if (!gitStatus) return []
    // 只保留文件变更：git 会把新增目录（路径以 / 结尾）也报为变更项，审查卡不展示目录
    return dedupeChanges(gitStatus.changes).filter((c) => !c.path.endsWith('/'))
  }, [gitStatus])
  const tree = useMemo(() => buildTree(changes), [changes])

  const toggleDir = (path: string) => {
    setCollapsedDirs((prev) => {
      const next = new Set(prev)
      if (next.has(path)) next.delete(path)
      else next.add(path)
      return next
    })
  }

  const totals = gitStatus?.totals

  return (
    <div style={{ padding: '6px 0' }}>
      {/* 变更汇总 */}
      {totals && totals.files > 0 && (
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '8px',
            padding: '4px 12px 8px',
            fontSize: '12px',
            color: 'var(--text-primary)',
          }}
        >
          <span>{totals.files} 个文件已变更</span>
          <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--success)' }}>
            +{totals.additions}
          </span>
          {totals.deletions > 0 && (
            <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--error)' }}>
              -{totals.deletions}
            </span>
          )}
        </div>
      )}

      {changes.length === 0 ? (
        <div style={{ fontSize: '12px', color: 'var(--text-tertiary)', padding: '4px 12px' }}>
          暂无待审查变更
        </div>
      ) : (
        <>
          {/* 根目录下的子目录 */}
          {[...tree.dirs.entries()].map(([name, node]) => (
            <DirBranch
              key={name}
              name={name}
              node={node}
              depth={0}
              pathPrefix=""
              collapsedDirs={collapsedDirs}
              onToggleDir={toggleDir}
              onFileOpen={onFileOpen}
            />
          ))}
          {/* 根目录下的文件 */}
          {tree.files.map((f) => (
            <FileRow key={f.path} change={f} depth={0} onFileOpen={onFileOpen} />
          ))}
        </>
      )}
    </div>
  )
}

export default ReviewCard
