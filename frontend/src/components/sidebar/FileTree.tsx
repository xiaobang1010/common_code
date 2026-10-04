import { useCallback, useEffect, useMemo, useRef, useState, type MouseEvent as ReactMouseEvent } from 'react'
import { filesApi } from '../../api/client'
import { subscribeFileEventsDebounced } from '../../api/fileEvents'
import { useWorkspaceSignal } from '../../stores/useWorkspaceSignal'
import {
  aggregateDirectoryStatus,
  buildStatusByPath,
  descendantStatuses,
  effectiveStatus,
  type GitFileStatus,
  type GitStatusIndex,
} from '../../utils/gitStatus'
import { useGitStatus } from '../inspector/useGitStatus'
import FileTreeContextMenu from './FileTreeContextMenu'
import { CHAT_INSERT_REF_EVENT } from '../ai/ChatInput'

// 文件列表接口返回的单项
interface FileItem {
  name: string
  type: 'dir' | 'file'
  path: string
  // 被 git 忽略（.gitignore 命中）：照常列出但淡化显示，字段缺失表示未被忽略
  ignored?: boolean
}

// 被忽略条目的文字色：与「占位、禁用」同一档灰，仅压暗不隐藏
const IGNORED_COLOR = 'var(--text-tertiary)'
// 被忽略条目的悬停提示：让灰色的含义可以自查
const IGNORED_HINT = '已被 .gitignore 忽略'

// git 状态 → 基色/徽标字母/中文名：基色是 index.css 的 token，组件内不写死色值
const STATUS_COLOR: Record<GitFileStatus, string> = {
  untracked: 'var(--git-untracked)',
  added: 'var(--git-added)',
  modified: 'var(--git-modified)',
  deleted: 'var(--git-deleted)',
  renamed: 'var(--git-renamed)',
}
const STATUS_BADGE: Record<GitFileStatus, string> = {
  modified: 'M',
  added: 'A',
  deleted: 'D',
  renamed: 'R',
  untracked: 'U',
}
const STATUS_LABEL: Record<GitFileStatus, string> = {
  modified: '修改',
  added: '新增',
  deleted: '删除',
  renamed: '重命名',
  untracked: '未跟踪',
}
// 徽标压到 70% 不透明度、目录圆点 60%：从基色 token 派生，避免另建色板
const badgeColor = (s: GitFileStatus) => `color-mix(in srgb, ${STATUS_COLOR[s]} 70%, transparent)`
const dotColor = (s: GitFileStatus) => `color-mix(in srgb, ${STATUS_COLOR[s]} 60%, transparent)`

// 工作区相对路径（正斜杠口径）拼成当前平台的绝对路径。
// 工作区路径本身来自主进程，已是原生分隔符口径；Windows 下统一转反斜杠
const toAbsolutePath = (workspacePath: string, relPath: string): string => {
  const isWin = navigator.userAgent.includes('Windows')
  const base = workspacePath.replace(/[\\/]+$/, '')
  return isWin ? `${base}\\${relPath.split('/').join('\\')}` : `${base}/${relPath}`
}

// 在系统文件管理器中定位路径：走 Electron 桥接，浏览器开发模式下静默跳过
const revealInFolder = (fullPath: string) => {
  const w = window as unknown as { electronAPI?: { revealInFolder?: (p: string) => Promise<void> } }
  void w.electronAPI?.revealInFolder?.(fullPath)
}

// 右键菜单锚点：节点屏幕坐标 + 目标节点相对路径
interface NodeMenuState {
  x: number
  y: number
  path: string
}

interface FileTreeProps {
  onFileOpen: (path: string) => void
  // 当前激活文件路径：树中对应节点高亮（编辑器树列使用，可不传）
  activePath?: string
  // 双击文件节点显式固定预览标签（打开后保留为正式标签）
  onPinFile?: (path: string) => void
  // 工作区显示名：头部标题行展示（侧栏文件树视图使用，可不传）
  workspaceName?: string
}

// 文件夹图标 SVG - color 由调用方给：被忽略的目录要跟着变灰，不能写死
function FolderIcon({ open, color }: { open: boolean; color: string }) {
  return open ? (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z" />
      <path d="M3 12h18" stroke="var(--border-strong)" strokeWidth="1" />
    </svg>
  ) : (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
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

// 过滤命中段高亮：名称中包含关键词的部分加深底色。
// 命中段颜色继承行文字色（状态色优先），只用底色与加粗表达命中，
// 避免灰底里冒出主色片段、或把 git 状态色洗回主文字色
function HighlightedName({ name, q, ignored }: { name: string; q: string; ignored?: boolean }) {
  const idx = name.toLowerCase().indexOf(q)
  if (idx < 0) return <span>{name}</span>
  return (
    <span>
      {name.slice(0, idx)}
      <span style={{ backgroundColor: 'var(--selected-bg)', color: ignored ? IGNORED_COLOR : 'inherit', fontWeight: 600 }}>{name.slice(idx, idx + q.length)}</span>
      {name.slice(idx + q.length)}
    </span>
  )
}

// 过滤模式下整树节点的完整结构（含全部子孙）
interface FullTreeNode {
  item: FileItem
  children: FullTreeNode[]
}

// 后端递归列表项转为前端树节点（ignored 要原样透传，否则过滤树永远读不到它）
const toFullNode = (it: { name: string; type: 'dir' | 'file'; path: string; ignored?: boolean; children?: unknown[] }): FullTreeNode => ({
  item: { name: it.name, type: it.type, path: it.path, ignored: it.ignored },
  children: (it.children || []).map((c) => toFullNode(c as { name: string; type: 'dir' | 'file'; path: string; ignored?: boolean; children?: unknown[] })),
})

// 一次性取整棵树：过滤激活时需要全局视角才能保留匹配项的父级目录链
const loadFullTree = async (): Promise<FullTreeNode[]> => {
  const res = await fetch('/api/files/list?path=.&recursive=true')
  const data = await res.json()
  return (data.items || []).map((it: { name: string; type: 'dir' | 'file'; path: string; ignored?: boolean; children?: unknown[] }) => toFullNode(it))
}

// 剪枝：只保留名称命中的节点及其父级目录链
const pruneTree = (nodes: FullTreeNode[], q: string): FullTreeNode[] =>
  nodes
    .map((n) => ({ ...n, children: pruneTree(n.children, q) }))
    .filter((n) => n.item.name.toLowerCase().includes(q) || n.children.length > 0)

// 变更路径集合：files 为归一后的文件路径；dirs 为未跟踪整目录条目
// （git porcelain 折叠输出 `dir/`，剥尾斜杠），其内文件按前缀命中
interface ChangedPaths {
  files: Set<string>
  dirs: Set<string>
}

// 剪枝：只保留「仅显示变更文件」命中的节点及其父级目录链。
// 文件按归一后路径精确命中，或落在某未跟踪目录条目前缀下；目录需有存活子节点
const pruneByChanged = (nodes: FullTreeNode[], changed: ChangedPaths): FullTreeNode[] =>
  nodes
    .map((n) => ({ ...n, children: pruneByChanged(n.children, changed) }))
    .filter((n) =>
      n.item.type === 'dir'
        ? n.children.length > 0
        : changed.files.has(n.item.path) ||
          [...changed.dirs].some((d) => n.item.path === d || n.item.path.startsWith(d + '/')),
    )

// 幽灵行注入（过滤树）：deleted 且磁盘快照里没有的文件插进父级文件段，
// 必须在剪枝之前做，否则仅含被删文件的目录会被整体剪掉；父目录不在树中则跳过
const injectPhantomNodes = (roots: FullTreeNode[], index: GitStatusIndex): FullTreeNode[] => {
  const deleted: string[] = []
  for (const [p, s] of index.statusByPath) {
    if (s === 'deleted') deleted.push(p)
  }
  if (deleted.length === 0) return roots
  const dirNodes = new Map<string, FullTreeNode>()
  const collect = (list: FullTreeNode[]) => {
    for (const n of list) {
      if (n.item.type === 'dir') {
        dirNodes.set(n.item.path, n)
        collect(n.children)
      }
    }
  }
  collect(roots)
  const touchedDirs = new Set<FullTreeNode>()
  let rootTouched = false
  for (const p of deleted) {
    const slash = p.lastIndexOf('/')
    const parentPath = slash < 0 ? '' : p.slice(0, slash)
    const name = slash < 0 ? p : p.slice(slash + 1)
    const parent = parentPath ? dirNodes.get(parentPath) : null
    const siblings = parentPath ? parent?.children : roots
    // 快照滞留下的同名常规行不重复插行，该行渲染时已按 deleted 着色
    if (!siblings || siblings.some((c) => c.item.path === p)) continue
    siblings.push({ item: { name, type: 'file', path: p }, children: [] })
    if (parent) touchedDirs.add(parent)
    else rootTouched = true
  }
  // 受影响层级重排：目录在前、文件段按名 code-unit 序，与懒树和列目录口径一致
  const resort = (list: FullTreeNode[]) => {
    list.sort((a, b) =>
      a.item.type === b.item.type ? byName(a.item, b.item) : a.item.type === 'dir' ? -1 : 1,
    )
    for (const n of list) resort(n.children)
  }
  if (rootTouched) resort(roots)
  for (const d of touchedDirs) resort(d.children)
  return roots
}

// 名称 code-unit 字典序比较：与后端列目录的排序口径一致，幽灵行插入文件段用
const byName = (a: { name: string }, b: { name: string }) => (a.name < b.name ? -1 : a.name > b.name ? 1 : 0)

// 单个树节点
interface FileTreeNodeProps {
  item: FileItem
  depth: number
  onFileOpen: (path: string) => void
  activePath?: string
  onPinFile?: (path: string) => void
  // 节点右键：弹出操作菜单（文件与目录均响应）
  onNodeContextMenu: (e: ReactMouseEvent, item: FileItem) => void
  // git 状态索引：随递归透传，着色/徽标/目录聚合/幽灵行共用同一份口径
  index: GitStatusIndex
}

function FileTreeNode({ item, depth, onFileOpen, activePath, onPinFile, onNodeContextMenu, index }: FileTreeNodeProps) {
  const [expanded, setExpanded] = useState(false)
  const [children, setChildren] = useState<FileItem[]>([])
  const [loaded, setLoaded] = useState(false)
  const [loading, setLoading] = useState(false)
  const [hovered, setHovered] = useState(false)

  const isDir = item.type === 'dir'
  // 当前打开文件的节点高亮，选中与视线在树内闭环
  const isActive = !isDir && activePath === item.path
  // 着色三级：忽略灰 > git 状态色 > 主文字色；激活与目录展开/折叠不再覆盖名称色。
  // 被忽略文件不参与未跟踪目录前缀命中（check-ignore 按索引判，被跟踪文件不会
  // 标忽略，忽略与状态两个来源互斥），整行淡灰、无徽标无圆点
  const status = !isDir && !item.ignored ? effectiveStatus(item.path, index) : null
  const dirAgg = isDir && !item.ignored ? aggregateDirectoryStatus(descendantStatuses(item.path, index)) : null
  const nameColor = item.ignored
    ? IGNORED_COLOR
    : status
      ? STATUS_COLOR[status]
      : dirAgg?.primary
        ? STATUS_COLOR[dirAgg.primary]
        : 'var(--text-primary)'
  // 删除态（幽灵行与磁盘快照滞后行同口径）：不可打开/固定、光标默认，右键菜单保留
  const isDeleted = status === 'deleted'

  // 幽灵行合并：deleted 且磁盘快照里没有的文件插进本目录文件段（目录之后按名排序）；
  // 渲染期计算不写回 children state，状态刷新后自动出现/消失；快照滞留下的同名
  // 常规行不重复插行，该行已按 deleted 着色
  const mergedChildren = useMemo(() => {
    if (!isDir) return children
    const prefix = item.path + '/'
    const existing = new Set(children.map((c) => c.path))
    const phantoms: FileItem[] = []
    for (const [p, s] of index.statusByPath) {
      const rel = p.startsWith(prefix) ? p.slice(prefix.length) : null
      if (s === 'deleted' && rel && !rel.includes('/') && !existing.has(p)) {
        phantoms.push({ name: rel, type: 'file', path: p })
      }
    }
    if (phantoms.length === 0) return children
    const dirs = children.filter((c) => c.type === 'dir')
    const files = children.filter((c) => c.type !== 'dir')
    return [...dirs, ...[...files, ...phantoms].sort(byName)]
  }, [children, index.statusByPath, isDir, item.path])

  const handleClick = async () => {
    if (isDeleted) return
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

  return (
    <div>
      <div
        onClick={handleClick}
        onDoubleClick={() => {
          if (!isDir && !isDeleted) onPinFile?.(item.path)
        }}
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
        onContextMenu={(e) => {
          onNodeContextMenu(e, item)
        }}
        title={item.ignored ? IGNORED_HINT : undefined}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '6px',
          paddingLeft: depth * 14 + 8,
          paddingRight: '8px',
          height: '26px',
          cursor: isDeleted ? 'default' : 'pointer',
          color: nameColor,
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
          {loading ? <LoadingIcon /> : isDir ? <FolderIcon open={expanded} color={nameColor} /> : <FileIcon color={nameColor} />}
        </span>
        <span style={{ flex: '1 1 auto', minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', fontWeight: isDir ? 500 : isActive ? 500 : 400 }}>{item.name}</span>
        {status && (
          <span
            title={STATUS_LABEL[status]}
            style={{ marginLeft: 'auto', flexShrink: 0, fontFamily: 'var(--font-mono)', fontWeight: 700, fontSize: '12px', lineHeight: 1, color: badgeColor(status) }}
          >
            {STATUS_BADGE[status]}
          </span>
        )}
        {dirAgg?.primary && (
          <span
            title={dirAgg.ordered.map((s) => STATUS_LABEL[s]).join('、')}
            style={{ marginLeft: 'auto', flexShrink: 0, width: 6, height: 6, borderRadius: '50%', background: dotColor(dirAgg.primary) }}
          />
        )}
      </div>
      {isDir && expanded && loaded && (
        <div>
          {mergedChildren.map((child) => (
            <FileTreeNode key={child.path} item={child} depth={depth + 1} onFileOpen={onFileOpen} activePath={activePath} onPinFile={onPinFile} onNodeContextMenu={onNodeContextMenu} index={index} />
          ))}
        </div>
      )}
    </div>
  )
}

// 过滤结果树节点：全部展开、命中高亮，点击文件打开；着色口径与普通树一致
function FilteredTreeNode({ node, depth, q, onFileOpen, activePath, onPinFile, onNodeContextMenu, index }: {
  node: FullTreeNode
  depth: number
  q: string
  onFileOpen: (path: string) => void
  activePath?: string
  onPinFile?: (path: string) => void
  onNodeContextMenu: (e: ReactMouseEvent, item: FileItem) => void
  index: GitStatusIndex
}) {
  const [hovered, setHovered] = useState(false)
  const isDir = node.item.type === 'dir'
  const isActive = !isDir && activePath === node.item.path
  const ignored = node.item.ignored
  const status = !isDir && !ignored ? effectiveStatus(node.item.path, index) : null
  const dirAgg = isDir && !ignored ? aggregateDirectoryStatus(descendantStatuses(node.item.path, index)) : null
  const nameColor = ignored
    ? IGNORED_COLOR
    : status
      ? STATUS_COLOR[status]
      : dirAgg?.primary
        ? STATUS_COLOR[dirAgg.primary]
        : 'var(--text-primary)'
  const isDeleted = status === 'deleted'
  return (
    <div>
      <div
        onClick={() => {
          if (!isDir && !isDeleted) onFileOpen(node.item.path)
        }}
        onDoubleClick={() => {
          if (!isDir && !isDeleted) onPinFile?.(node.item.path)
        }}
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
        onContextMenu={(e) => {
          onNodeContextMenu(e, node.item)
        }}
        title={ignored ? IGNORED_HINT : undefined}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '6px',
          paddingLeft: depth * 14 + 8,
          paddingRight: '8px',
          height: '26px',
          cursor: isDir || isDeleted ? 'default' : 'pointer',
          color: nameColor,
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
          {isDir ? <FolderIcon open={true} color={nameColor} /> : <FileIcon color={nameColor} />}
        </span>
        <span style={{ flex: '1 1 auto', minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', fontWeight: isDir ? 500 : isActive ? 500 : 400 }}>
          <HighlightedName name={node.item.name} q={q} ignored={ignored} />
        </span>
        {status && (
          <span
            title={STATUS_LABEL[status]}
            style={{ marginLeft: 'auto', flexShrink: 0, fontFamily: 'var(--font-mono)', fontWeight: 700, fontSize: '12px', lineHeight: 1, color: badgeColor(status) }}
          >
            {STATUS_BADGE[status]}
          </span>
        )}
        {dirAgg?.primary && (
          <span
            title={dirAgg.ordered.map((s) => STATUS_LABEL[s]).join('、')}
            style={{ marginLeft: 'auto', flexShrink: 0, width: 6, height: 6, borderRadius: '50%', background: dotColor(dirAgg.primary) }}
          />
        )}
      </div>
      {node.children.map((c) => (
        <FilteredTreeNode key={c.item.path} node={c} depth={depth + 1} q={q} onFileOpen={onFileOpen} activePath={activePath} onPinFile={onPinFile} onNodeContextMenu={onNodeContextMenu} index={index} />
      ))}
    </div>
  )
}

function FileTree({ onFileOpen, activePath, onPinFile, workspaceName }: FileTreeProps) {
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

  // 「仅显示变更文件」：打开后整树只保留 git 变更文件及其祖先目录。
  // 变更集合与搜索框同时生效（两道过滤取交集）
  const [showChangedOnly, setShowChangedOnly] = useState(false)
  const [refreshTick, setRefreshTick] = useState(0)
  const git = useGitStatus()

  // git 状态索引：仓库根口径的 changes 按 repoPrefix 归一为工作区相对口径、
  // 同路径多状态按档位合并；着色/徽标/聚合/幽灵行/变更过滤共用同一份数据
  const statusIndex = useMemo(
    () => buildStatusByPath(git.data?.changes ?? [], git.data?.repoPrefix ?? ''),
    [git.data],
  )
  // 「仅显示变更文件」的命中集合由同一索引派生，不再各自手工归一
  const changedPaths = useMemo<ChangedPaths>(
    () => ({ files: new Set(statusIndex.statusByPath.keys()), dirs: statusIndex.untrackedDirs }),
    [statusIndex],
  )
  // 根层幽灵行：路径不含斜杠的 deleted 文件插进根列表文件段（目录之后按名排序），
  // 与子目录合并同一口径，工作区根层被删文件不会无处出现
  const rootMerged = useMemo(() => {
    const existing = new Set(rootItems.map((i) => i.path))
    const phantoms: FileItem[] = []
    for (const [p, s] of statusIndex.statusByPath) {
      if (s === 'deleted' && !p.includes('/') && !existing.has(p)) {
        phantoms.push({ name: p, type: 'file', path: p })
      }
    }
    if (phantoms.length === 0) return rootItems
    const dirs = rootItems.filter((i) => i.type === 'dir')
    const files = rootItems.filter((i) => i.type !== 'dir')
    return [...dirs, ...[...files, ...phantoms].sort(byName)]
  }, [rootItems, statusIndex])

  useEffect(() => {
    const q = filter.trim().toLowerCase()
    const needFullTree = !!q || showChangedOnly
    if (!needFullTree) {
      setFilterTree(null)
      setFilterLoading(false)
      return
    }
    let cancelled = false
    setFilterLoading(true)
    loadFullTree()
      .then((tree) => {
        if (cancelled) return
        // 幽灵行先注入再剪枝，保证仅含被删文件的目录不被剪掉
        let result = injectPhantomNodes(tree, statusIndex)
        if (showChangedOnly) result = pruneByChanged(result, changedPaths)
        if (q) result = pruneTree(result, q)
        setFilterTree(result)
        setFilterLoading(false)
      })
      .catch(() => {
        if (!cancelled) setFilterLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [filter, showChangedOnly, changedPaths, statusIndex, refreshTick])

  // 请求代号：每次发起递增，响应回来对不上号说明已发出更新的请求（如事件风暴
  // 期间叠加工作区快速切换），过期响应直接丢弃，避免旧数据覆盖新数据
  const genRef = useRef(0)

  const loadRoot = useCallback(async () => {
    const gen = ++genRef.current
    setRefreshing(true)
    try {
      const res = await fetch('/api/files/list?path=.')
      const data = await res.json()
      if (gen !== genRef.current) return
      setRootItems(data.items || [])
      setError('')
    } catch (e) {
      // 刷新失败保留上次数据，仅记录错误供轻量提示；首开失败才显示全量错误块
      if (gen !== genRef.current) return
      setError('加载失败')
      console.error(e)
    } finally {
      // 只有最新一次请求有权收尾，避免旧请求把新请求的 refreshing 提前掐灭
      if (gen === genRef.current) {
        setLoading(false)
        setRefreshing(false)
      }
    }
  }, [])

  // 手动刷新：重拉根层、bump 版本重置懒加载展开态、触发过滤分支重算
  const refreshTree = useCallback(() => {
    setTreeVersion((v) => v + 1)
    setRefreshTick((t) => t + 1)
    void loadRoot()
  }, [loadRoot])

  // 当前工作区路径信号：首次挂载与切换工作区共用一条重取链路。切换时先清数据，
  // 重取完成前不闪现上一个工作区的树（对齐 useGitStatus 的先清后取模式）
  const workspacePath = useWorkspaceSignal((s) => s.currentPath)

  // ---- 树节点右键菜单（文件与目录通用）----
  const [nodeMenu, setNodeMenu] = useState<NodeMenuState | null>(null)

  const handleNodeContextMenu = useCallback((e: ReactMouseEvent, item: FileItem) => {
    e.preventDefault()
    // 阻止冒泡到树容器，避免同一事件里外层再处理一次
    e.stopPropagation()
    setNodeMenu({ x: e.clientX, y: e.clientY, path: item.path })
  }, [])

  const handleReveal = useCallback(() => {
    if (!nodeMenu || !workspacePath) return
    revealInFolder(toAbsolutePath(workspacePath, nodeMenu.path))
  }, [nodeMenu, workspacePath])

  const handleCopyAbsolute = useCallback(() => {
    if (!nodeMenu || !workspacePath) return
    void navigator.clipboard.writeText(toAbsolutePath(workspacePath, nodeMenu.path))
  }, [nodeMenu, workspacePath])

  const handleCopyRelative = useCallback(() => {
    if (!nodeMenu) return
    void navigator.clipboard.writeText(nodeMenu.path)
  }, [nodeMenu])

  const handleAddToChat = useCallback(() => {
    if (!nodeMenu) return
    // 通知对话输入框在光标处（或末尾）插入内联文件引用 chip
    window.dispatchEvent(new CustomEvent(CHAT_INSERT_REF_EVENT, { detail: nodeMenu.path }))
  }, [nodeMenu])

  useEffect(() => {
    setRootItems([])
    setLoading(true)
    void loadRoot()
  }, [loadRoot, workspacePath])

  // 订阅文件变更事件：AI 写盘后刷新文件树（防抖合并连续写盘的风暴）；
  // 断线重连成功时也立即刷新一次兜底
  useEffect(
    () =>
      subscribeFileEventsDebounced(() => void loadRoot(), {
        onOpen: () => void loadRoot(),
      }),
    [loadRoot],
  )

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

      {/* 工作区标题行：工作区名 + 「仅显示变更文件」「刷新文件树」两个默认按钮 */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '6px',
          padding: '8px 8px 4px',
        }}
      >
        <span
          style={{
            fontSize: '12px',
            fontFamily: 'var(--font-ui)',
            fontWeight: 600,
            color: 'var(--text-primary)',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
            minWidth: 0,
          }}
          title={workspaceName}
        >
          {workspaceName}
        </span>
        <span style={{ flex: 1 }} />
        <button
          onClick={() => setShowChangedOnly((v) => !v)}
          title="仅显示变更文件"
          style={{
            width: '22px',
            height: '22px',
            flexShrink: 0,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            border: '1px solid var(--border)',
            borderRadius: 'var(--radius-sm)',
            background: showChangedOnly ? 'var(--selected-bg)' : 'transparent',
            color: showChangedOnly ? 'var(--text-primary)' : 'var(--text-tertiary)',
            cursor: 'pointer',
            transition: 'all var(--transition-fast)',
            padding: 0,
          }}
        >
          {/* 筛选漏斗图标 */}
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <path d="M3 5h18l-7 8v5l-4 2v-7z" />
          </svg>
        </button>
        <button
          onClick={refreshTree}
          title="刷新文件树"
          style={{
            width: '22px',
            height: '22px',
            flexShrink: 0,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            border: '1px solid var(--border)',
            borderRadius: 'var(--radius-sm)',
            background: refreshing ? 'var(--selected-bg)' : 'transparent',
            color: 'var(--text-tertiary)',
            cursor: 'pointer',
            transition: 'all var(--transition-fast)',
            padding: 0,
          }}
        >
          {/* 环形刷新箭头 */}
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <path d="M21 12a9 9 0 1 1-2.64-6.36" />
            <path d="M21 3v6h-6" />
          </svg>
        </button>
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
          <FolderIcon open={false} color="var(--text-secondary)" />
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
        {rootMerged.map((item) => (
          <FileTreeNode key={item.path} item={item} depth={0} onFileOpen={onFileOpen} activePath={activePath} onPinFile={onPinFile} onNodeContextMenu={handleNodeContextMenu} index={statusIndex} />
        ))}
      </div>
      {filterTree !== null && (
        <div style={{ flex: 1, overflow: 'auto', padding: '6px 0' }}>
          {filterLoading && filterTree.length === 0 ? (
            <div style={{ padding: '8px 16px', color: 'var(--text-tertiary)', fontSize: '12px' }}>过滤中…</div>
          ) : filterTree.length === 0 ? (
            <div style={{ padding: '8px 16px', color: 'var(--text-tertiary)', fontSize: '12px' }}>
              {showChangedOnly && !filter.trim() ? '无变更文件' : '没有匹配的文件'}
            </div>
          ) : (
            filterTree.map((n) => (
              <FilteredTreeNode key={n.item.path} node={n} depth={0} q={filter.trim().toLowerCase()} onFileOpen={onFileOpen} activePath={activePath} onPinFile={onPinFile} onNodeContextMenu={handleNodeContextMenu} index={statusIndex} />
            ))
          )}
        </div>
      )}

      {/* 树节点右键菜单浮层 */}
      {nodeMenu && (
        <FileTreeContextMenu
          x={nodeMenu.x}
          y={nodeMenu.y}
          onClose={() => setNodeMenu(null)}
          onReveal={handleReveal}
          onCopyAbsolute={handleCopyAbsolute}
          onCopyRelative={handleCopyRelative}
          onAddToChat={handleAddToChat}
        />
      )}
    </div>
  )
}

export default FileTree
