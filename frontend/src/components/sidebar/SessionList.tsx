import { useEffect, useState } from 'react'
import type { SessionGroup, SessionInfo } from '../../api/client'

interface SessionListProps {
  groups: SessionGroup[]
  currentWorkspacePath: string | null
  currentSessionId: string | null
  // 当前运行任务所属会话（列表显示运行指示）
  runningSessionId: string | null
  onCreate: (workspacePath: string) => void
  onSwitch: (sessionId: string) => void
  onSwitchInWorkspace: (sessionId: string, workspacePath: string) => void
  onDelete: (sessionId: string) => void
  onRemoveWorkspace: (workspacePath: string) => void
  onOpenWorkspace: () => void
  onRename: (sessionId: string, title: string) => void
  onTogglePin: (sessionId: string, pinned: boolean) => void
  onUpdateWorkspace: (path: string, data: { alias?: string; pinned?: boolean }) => void
}

// 把 ISO 时间字符串转成相对时间描述，如"3分钟前"
function relativeTime(isoStr: string): string {
  const now = Date.now()
  const then = new Date(isoStr).getTime()
  const diff = Math.max(0, now - then)
  const seconds = Math.floor(diff / 1000)
  const minutes = Math.floor(seconds / 60)
  const hours = Math.floor(minutes / 60)
  const days = Math.floor(hours / 24)

  if (days > 7) return new Date(isoStr).toLocaleDateString('zh-CN')
  if (days > 0) return `${days}天前`
  if (hours > 0) return `${hours}小时前`
  if (minutes > 0) return `${minutes}分钟前`
  return '刚刚'
}

// 从路径中提取最后一段作为工作区名称
function basename(path: string): string {
  const parts = path.replace(/\\/g, '/').split('/')
  return parts[parts.length - 1] || path
}

// 工作区显示名：别名优先，其次 name，最后路径末段
function displayName(ws: { alias: string; name: string; path: string }): string {
  return ws.alias || ws.name || basename(ws.path)
}

// 任务排序：置顶优先，再按 updated_at 降序（分组视图复用）
export function sortSessions(sessions: SessionInfo[]): SessionInfo[] {
  return [...sessions].sort((a, b) => {
    if (a.pinned !== b.pinned) return a.pinned ? -1 : 1
    return new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime()
  })
}

// 菜单项统一样式
const menuItemStyle: React.CSSProperties = {
  width: '100%',
  padding: '7px 12px',
  border: 'none',
  background: 'transparent',
  color: 'var(--text-secondary)',
  fontSize: '12px',
  fontFamily: 'var(--font-ui)',
  cursor: 'pointer',
  textAlign: 'left',
  display: 'flex',
  alignItems: 'center',
  gap: '6px',
  transition: 'all var(--transition-fast)',
}

// 单个任务条目（单行：状态图标 + 标题 + 右对齐相对时间）
// enableDrag 为 true 时可拖拽（分组视图拖入分组/拖回未分组用），
// 拖拽数据经 dataTransfer 的 'text/session-id' 传递
export function SessionItem({
  session,
  isActive,
  isRunning,
  enableDrag = false,
  onSwitch,
  onDelete,
  onRename,
  onTogglePin,
}: {
  session: SessionInfo
  isActive: boolean
  isRunning: boolean
  enableDrag?: boolean
  onSwitch: (id: string) => void
  onDelete: (id: string) => void
  onRename: (id: string, title: string) => void
  onTogglePin: (id: string, pinned: boolean) => void
}) {
  const [hovered, setHovered] = useState(false)
  const [menuOpen, setMenuOpen] = useState(false)
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(session.title)

  const commitRename = () => {
    const trimmed = draft.trim()
    if (trimmed && trimmed !== session.title) onRename(session.id, trimmed)
    setEditing(false)
  }

  // 重命名编辑态：整行变为输入框
  if (editing) {
    return (
      <div style={{ padding: '4px 10px 4px 14px' }}>
        <input
          autoFocus
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commitRename}
          onKeyDown={(e) => {
            if (e.key === 'Enter') commitRename()
            if (e.key === 'Escape') setEditing(false)
          }}
          style={{
            width: '100%',
            padding: '3px 6px',
            border: '1px solid var(--border-strong)',
            borderRadius: 'var(--radius-sm)',
            background: 'var(--bg-base)',
            color: 'var(--text-primary)',
            fontSize: '12px',
            fontFamily: 'var(--font-ui)',
            outline: 'none',
            boxShadow: '0 0 0 3px var(--focus-ring)',
          }}
        />
      </div>
    )
  }

  return (
    <div
      onClick={() => onSwitch(session.id)}
      draggable={enableDrag}
      onDragStart={(e) => {
        e.dataTransfer.setData('text/session-id', session.id)
        e.dataTransfer.effectAllowed = 'move'
      }}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => { setHovered(false); setMenuOpen(false) }}
      style={{
        position: 'relative',
        padding: '6px 10px 6px 14px',
        cursor: 'pointer',
        borderRadius: 'var(--radius-sm)',
        marginBottom: '1px',
        backgroundColor: isActive
          ? 'var(--selected-bg)'
          : hovered
            ? 'var(--hover-bg)'
            : 'transparent',
        transition: 'background var(--transition-fast)',
        // 当前任务左侧中性灰边条
        borderLeft: isActive
          ? '3px solid var(--border-strong)'
          : '3px solid transparent',
      }}
    >
      {/* 单行：状态图标 + 标题 + 右对齐相对时间 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
        {/* 运行中转圈 / 置顶图钉 */}
        {isRunning ? (
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="var(--text-primary)" strokeWidth="2.5" strokeLinecap="round" style={{ animation: 'spin 1s linear infinite', flexShrink: 0 }}>
            <path d="M21 12a9 9 0 1 1-6.219-8.56" />
          </svg>
        ) : session.pinned ? (
          <svg width="11" height="11" viewBox="0 0 24 24" fill="var(--text-secondary)" style={{ flexShrink: 0 }}>
            <path d="M16 3l5 5-8 2-4 4-2-2 4-4 2-8z" transform="rotate(45 12 12)" />
          </svg>
        ) : null}
        <span
          style={{
            flex: 1,
            color: isActive ? 'var(--text-primary)' : 'var(--text-secondary)',
            fontSize: '12px',
            fontFamily: 'var(--font-ui)',
            fontWeight: 500,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
            minWidth: 0,
          }}
          title={`${session.title || '新任务'}${session.message_count > 0 ? ` · ${session.message_count} 条消息` : ''}`}
        >
          {session.title || '新任务'}
        </span>
        <span
          style={{
            flexShrink: 0,
            color: 'var(--text-tertiary)',
            fontSize: '10px',
            fontFamily: 'var(--font-mono)',
          }}
        >
          {relativeTime(session.updated_at)}
        </span>
        {/* hover 菜单按钮 */}
        <button
          onClick={(e) => {
            e.stopPropagation()
            setMenuOpen(prev => !prev)
          }}
          title="更多操作"
          style={{
            flexShrink: 0,
            padding: '2px 4px',
            border: 'none',
            background: 'transparent',
            color: 'var(--text-tertiary)',
            cursor: 'pointer',
            borderRadius: 'var(--radius-sm)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            opacity: hovered ? 1 : 0,
          }}
        >
          <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor">
            <circle cx="5" cy="12" r="1.5" />
            <circle cx="12" cy="12" r="1.5" />
            <circle cx="19" cy="12" r="1.5" />
          </svg>
        </button>
        {/* 下拉菜单：重命名 / 置顶 / 删除 */}
        {menuOpen && (
          <div
            onClick={(e) => e.stopPropagation()}
            style={{
              position: 'absolute',
              top: '100%',
              right: '0',
              marginTop: '2px',
              backgroundColor: 'var(--bg-elevated)',
              border: '1px solid var(--border-strong)',
              borderRadius: 'var(--radius-sm)',
              boxShadow: 'var(--shadow-lg)',
              zIndex: 100,
              minWidth: '120px',
              overflow: 'hidden',
            }}
          >
            <button
              onClick={() => { setMenuOpen(false); setDraft(session.title); setEditing(true) }}
              style={menuItemStyle}
              onMouseEnter={(e) => (e.currentTarget.style.backgroundColor = 'var(--bg-tertiary)')}
              onMouseLeave={(e) => (e.currentTarget.style.backgroundColor = 'transparent')}
            >
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                <path d="M17 3a2.8 2.8 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z" />
              </svg>
              重命名
            </button>
            <button
              onClick={() => { setMenuOpen(false); onTogglePin(session.id, !session.pinned) }}
              style={menuItemStyle}
              onMouseEnter={(e) => (e.currentTarget.style.backgroundColor = 'var(--bg-tertiary)')}
              onMouseLeave={(e) => (e.currentTarget.style.backgroundColor = 'transparent')}
            >
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 17v5M5 9h14M7 9V4h10v5M8 9l1 8h6l1-8" />
              </svg>
              {session.pinned ? '取消置顶' : '置顶'}
            </button>
            <button
              onClick={() => { setMenuOpen(false); onDelete(session.id) }}
              style={menuItemStyle}
              onMouseEnter={(e) => {
                e.currentTarget.style.backgroundColor = 'var(--error-soft)'
                e.currentTarget.style.color = 'var(--error)'
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.backgroundColor = 'transparent'
                e.currentTarget.style.color = 'var(--text-secondary)'
              }}
            >
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                <path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2m3 0v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6h14z" />
                <path d="M10 11v6M14 11v6" />
              </svg>
              删除任务
            </button>
          </div>
        )}
      </div>
    </div>
  )
}

// 工作区分组组件：可折叠/展开
function WorkspaceGroup({
  group,
  isCurrent,
  currentSessionId,
  runningSessionId,
  onCreate,
  onSwitch,
  onSwitchInWorkspace,
  onDelete,
  onRemoveWorkspace,
  onRename,
  onTogglePin,
  onUpdateWorkspace,
}: {
  group: SessionGroup
  isCurrent: boolean
  currentSessionId: string | null
  runningSessionId: string | null
  onCreate: (workspacePath: string) => void
  onSwitch: (sessionId: string) => void
  onSwitchInWorkspace: (sessionId: string, workspacePath: string) => void
  onDelete: (sessionId: string) => void
  onRemoveWorkspace: (workspacePath: string) => void
  onRename: (sessionId: string, title: string) => void
  onTogglePin: (sessionId: string, pinned: boolean) => void
  onUpdateWorkspace: (path: string, data: { alias?: string; pinned?: boolean }) => void
}) {
  // 当前工作区默认展开，其他默认折叠
  const [expanded, setExpanded] = useState(isCurrent)
  // 成为当前工作区时自动展开：跨工作区「+」新建会切到目标工作区，展开才能看到新任务
  useEffect(() => {
    if (isCurrent) setExpanded(true)
  }, [isCurrent])
  const [headerHovered, setHeaderHovered] = useState(false)
  const [menuOpen, setMenuOpen] = useState(false)
  const [aliasEditing, setAliasEditing] = useState(false)
  const [aliasDraft, setAliasDraft] = useState(group.workspace.alias || '')
  // 「+」按钮悬停态：控制「新建任务」提示气泡显隐
  const [plusHovered, setPlusHovered] = useState(false)
  // 提示气泡锚点：悬停时记录的按钮视口坐标。气泡用 fixed 定位挂在锚点上，
  // 恒显示于按钮上方，且不受列表滚动容器 overflow 裁剪
  const [plusAnchor, setPlusAnchor] = useState<{ right: number; bottom: number } | null>(null)

  const ws = group.workspace
  const commitAlias = () => {
    const trimmed = aliasDraft.trim()
    if (trimmed !== ws.alias) onUpdateWorkspace(ws.path, { alias: trimmed })
    setAliasEditing(false)
  }

  const copyPath = () => {
    navigator.clipboard?.writeText(ws.path).catch(() => {})
    setMenuOpen(false)
  }

  return (
    <div style={{ marginBottom: '2px' }}>
      {/* 分组标题行 */}
      <div
        onClick={() => setExpanded(prev => !prev)}
        onMouseEnter={() => setHeaderHovered(true)}
        onMouseLeave={() => { setHeaderHovered(false); setMenuOpen(false); setPlusHovered(false) }}
        style={{
          display: 'flex',
          alignItems: 'center',
          padding: '6px 10px 6px 8px',
          cursor: 'pointer',
          borderRadius: 'var(--radius-sm)',
          backgroundColor: isCurrent
            ? 'var(--selected-bg)'
            : headerHovered
              ? 'var(--hover-bg)'
              : 'transparent',
          transition: 'background var(--transition-fast)',
          userSelect: 'none',
          position: 'relative',
        }}
      >
        {/* 折叠箭头 */}
        <span
          style={{
            display: 'inline-block',
            width: '14px',
            fontSize: '10px',
            color: isCurrent ? 'var(--text-primary)' : 'var(--text-tertiary)',
            transition: 'transform var(--transition-fast)',
            transform: expanded ? 'rotate(90deg)' : 'rotate(0deg)',
            flexShrink: 0,
          }}
        >
          {'\u25B8'}
        </span>
        {/* 置顶图钉 + 文件夹图标（对齐目标 UI：项目条目带文件夹标识） */}
        {ws.pinned && (
          <svg width="10" height="10" viewBox="0 0 24 24" fill="var(--text-secondary)" style={{ flexShrink: 0, marginRight: '2px' }}>
            <path d="M16 3l5 5-8 2-4 4-2-2 4-4 2-8z" transform="rotate(45 12 12)" />
          </svg>
        )}
        <svg
          width="12"
          height="12"
          viewBox="0 0 24 24"
          fill="none"
          stroke={isCurrent ? 'var(--text-primary)' : 'var(--text-secondary)'}
          strokeWidth="1.8"
          strokeLinecap="round"
          strokeLinejoin="round"
          style={{ flexShrink: 0, marginRight: '2px' }}
        >
          <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z" />
        </svg>
        <span
          style={{
            flex: 1,
            fontSize: '12px',
            fontFamily: 'var(--font-ui)',
            fontWeight: 600,
            color: isCurrent ? 'var(--text-primary)' : 'var(--text-secondary)',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
            minWidth: 0,
          }}
          title={ws.path}
        >
          {displayName(ws)}
        </span>
        {/* 最后活跃相对时间（悬停时隐藏，为「⋯」「+」按钮腾位） */}
        {!headerHovered && (
          <span
            style={{
              fontSize: '10px',
              color: 'var(--text-tertiary)',
              fontFamily: 'var(--font-mono)',
              flexShrink: 0,
              marginLeft: '6px',
            }}
          >
            {relativeTime(ws.last_used_at)}
          </span>
        )}
        {/* 任务数量（同上，悬停时隐藏） */}
        {!headerHovered && (
          <span
            style={{
              fontSize: '10px',
              color: 'var(--text-tertiary)',
              fontFamily: 'var(--font-ui)',
              flexShrink: 0,
              marginLeft: '6px',
            }}
          >
            {group.sessions.length} 个任务
          </span>
        )}
        {/* 省略号按钮 - hover 时显示 */}
        {headerHovered && (
          <button
            onClick={(e) => {
              e.stopPropagation()
              setMenuOpen(prev => !prev)
            }}
            title="更多操作"
            style={{
              flexShrink: 0,
              padding: '2px 4px',
              border: 'none',
              background: menuOpen ? 'var(--bg-tertiary)' : 'transparent',
              color: 'var(--text-tertiary)',
              cursor: 'pointer',
              borderRadius: 'var(--radius-sm)',
              transition: 'all var(--transition-fast)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              marginLeft: '4px',
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.color = 'var(--text-primary)'
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.color = 'var(--text-tertiary)'
            }}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
              <circle cx="5" cy="12" r="1.5" />
              <circle cx="12" cy="12" r="1.5" />
              <circle cx="19" cy="12" r="1.5" />
            </svg>
          </button>
        )}
        {/* 新建任务：悬停任意工作区行时浮现的气泡按钮；非当前工作区点击 = 先切过去再建 */}
        {headerHovered && (
          <button
            onClick={(e) => {
              e.stopPropagation()
              onCreate(ws.path)
            }}
            style={{
              flexShrink: 0,
              width: '18px',
              height: '18px',
              marginLeft: '4px',
              padding: 0,
              border: '1px solid var(--border-strong)',
              borderRadius: 'var(--radius-sm)',
              background: 'transparent',
              color: 'var(--text-secondary)',
              cursor: 'pointer',
              transition: 'all var(--transition-fast)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.backgroundColor = 'var(--bg-tertiary)'
              e.currentTarget.style.color = 'var(--text-primary)'
              // 视口坐标锚定气泡：右缘对齐按钮，bottom 抬到按钮上方 4px
              const r = e.currentTarget.getBoundingClientRect()
              setPlusAnchor({
                right: window.innerWidth - r.right,
                bottom: window.innerHeight - r.top + 4,
              })
              setPlusHovered(true)
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.backgroundColor = 'transparent'
              e.currentTarget.style.color = 'var(--text-secondary)'
              setPlusHovered(false)
            }}
          >
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <path d="M12 5v14M5 12h14" />
            </svg>
          </button>
        )}
        {/* 「新建任务」提示气泡：仅悬停「+」按钮时出现，fixed 定位恒在按钮上方；
            不响应鼠标，防悬停闪烁 */}
        {plusHovered && headerHovered && plusAnchor && (
          <div
            style={{
              position: 'fixed',
              right: plusAnchor.right,
              bottom: plusAnchor.bottom,
              padding: '3px 8px',
              backgroundColor: 'var(--bg-elevated)',
              border: '1px solid var(--border-strong)',
              borderRadius: 'var(--radius-sm)',
              boxShadow: 'var(--shadow-lg)',
              color: 'var(--text-primary)',
              fontSize: '11px',
              fontFamily: 'var(--font-ui)',
              whiteSpace: 'nowrap',
              zIndex: 100,
              pointerEvents: 'none',
            }}
          >
            新建任务
          </div>
        )}
        {/* 下拉菜单：置顶 / 别名重命名 / 复制路径 / 移除 */}
        {menuOpen && (
          <div
            onClick={(e) => e.stopPropagation()}
            style={{
              position: 'absolute',
              top: '100%',
              right: '0',
              marginTop: '2px',
              backgroundColor: 'var(--bg-elevated)',
              border: '1px solid var(--border-strong)',
              borderRadius: 'var(--radius-sm)',
              boxShadow: 'var(--shadow-lg)',
              zIndex: 100,
              minWidth: '130px',
              overflow: 'hidden',
            }}
          >
            {aliasEditing ? (
              <div style={{ padding: '6px 8px' }}>
                <input
                  autoFocus
                  value={aliasDraft}
                  onChange={(e) => setAliasDraft(e.target.value)}
                  onBlur={commitAlias}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') commitAlias()
                    if (e.key === 'Escape') setAliasEditing(false)
                  }}
                  placeholder="别名（留空取消）"
                  style={{
                    width: '100%',
                    padding: '3px 6px',
                    border: '1px solid var(--border-strong)',
                    borderRadius: 'var(--radius-sm)',
                    background: 'var(--bg-base)',
                    color: 'var(--text-primary)',
                    fontSize: '12px',
                    fontFamily: 'var(--font-ui)',
                    outline: 'none',
                    boxShadow: '0 0 0 3px var(--focus-ring)',
                  }}
                />
              </div>
            ) : (
              <>
                <button
                  onClick={() => { setMenuOpen(false); onUpdateWorkspace(ws.path, { pinned: !ws.pinned }) }}
                  style={menuItemStyle}
                  onMouseEnter={(e) => (e.currentTarget.style.backgroundColor = 'var(--bg-tertiary)')}
                  onMouseLeave={(e) => (e.currentTarget.style.backgroundColor = 'transparent')}
                >
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M12 17v5M5 9h14M7 9V4h10v5M8 9l1 8h6l1-8" />
                  </svg>
                  {ws.pinned ? '取消置顶' : '置顶'}
                </button>
                <button
                  onClick={() => { setAliasDraft(ws.alias); setAliasEditing(true) }}
                  style={menuItemStyle}
                  onMouseEnter={(e) => (e.currentTarget.style.backgroundColor = 'var(--bg-tertiary)')}
                  onMouseLeave={(e) => (e.currentTarget.style.backgroundColor = 'transparent')}
                >
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M17 3a2.8 2.8 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z" />
                  </svg>
                  重命名别名
                </button>
                <button
                  onClick={copyPath}
                  style={menuItemStyle}
                  onMouseEnter={(e) => (e.currentTarget.style.backgroundColor = 'var(--bg-tertiary)')}
                  onMouseLeave={(e) => (e.currentTarget.style.backgroundColor = 'transparent')}
                >
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                    <rect x="9" y="9" width="13" height="13" rx="2" />
                    <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
                  </svg>
                  复制路径
                </button>
                <button
                  onClick={() => { setMenuOpen(false); onRemoveWorkspace(ws.path) }}
                  style={menuItemStyle}
                  onMouseEnter={(e) => {
                    e.currentTarget.style.backgroundColor = 'var(--error-soft)'
                    e.currentTarget.style.color = 'var(--error)'
                  }}
                  onMouseLeave={(e) => {
                    e.currentTarget.style.backgroundColor = 'transparent'
                    e.currentTarget.style.color = 'var(--text-secondary)'
                  }}
                >
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2m3 0v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6h14z" />
                    <path d="M10 11v6M14 11v6" />
                  </svg>
                  移除工作区及其项目
                </button>
              </>
            )}
          </div>
        )}
      </div>

      {/* 展开内容 */}
      {expanded && (
        <div style={{ paddingLeft: '4px' }}>
          {/* 任务列表（置顶优先排序） */}
          {group.sessions.length === 0 ? (
            <div
              style={{
                padding: '12px 12px 12px 18px',
                color: 'var(--text-tertiary)',
                fontSize: '12px',
                fontFamily: 'var(--font-ui)',
              }}
            >
              暂无项目
            </div>
          ) : (
            sortSessions(group.sessions).map((session) => (
              <SessionItem
                key={session.id}
                session={session}
                isActive={session.id === currentSessionId}
                isRunning={session.id === runningSessionId}
                onSwitch={(id) => {
                  if (isCurrent) {
                    onSwitch(id)
                  } else {
                    onSwitchInWorkspace(id, group.workspace.path)
                  }
                }}
                onDelete={onDelete}
                onRename={onRename}
                onTogglePin={onTogglePin}
              />
            ))
          )}
        </div>
      )}
    </div>
  )
}

function SessionList({
  groups,
  currentWorkspacePath,
  currentSessionId,
  runningSessionId,
  onCreate,
  onSwitch,
  onSwitchInWorkspace,
  onDelete,
  onRemoveWorkspace,
  onOpenWorkspace,
  onRename,
  onTogglePin,
  onUpdateWorkspace,
}: SessionListProps) {
  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        overflow: 'hidden',
      }}
    >
      {/* 分组列表 */}
      <div
        style={{
          flex: 1,
          overflowY: 'auto',
          padding: '6px 4px',
        }}
      >
        {groups.length === 0 ? (
          <div
            style={{
              padding: '40px 16px',
              textAlign: 'center',
              color: 'var(--text-tertiary)',
              fontSize: '12px',
              fontFamily: 'var(--font-ui)',
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              gap: '16px',
            }}
          >
            <div style={{ color: 'var(--text-tertiary)', fontSize: '13px' }}>
              还没有打开任何工作区
            </div>
            <div style={{ color: 'var(--text-tertiary)', fontSize: '11px', lineHeight: 1.6 }}>
              打开一个工作区后，AI 会在其中读代码、改文件、跑命令
            </div>
            <button
              onClick={onOpenWorkspace}
              style={{
                padding: '8px 16px',
                border: '1px solid var(--border-strong)',
                borderRadius: 'var(--radius-md)',
                backgroundColor: 'var(--button-primary-bg)',
                color: 'var(--button-primary-text)',
                fontSize: '12px',
                fontFamily: 'var(--font-ui)',
                fontWeight: 500,
                cursor: 'pointer',
                transition: 'all var(--transition-fast)',
                display: 'flex',
                alignItems: 'center',
                gap: '6px',
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.backgroundColor = 'var(--button-primary-bg-hover)'
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.backgroundColor = 'var(--button-primary-bg)'
              }}
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z" />
                <path d="M9 13h6" />
              </svg>
              打开工作区
            </button>
          </div>
        ) : (
          groups.map((group) => (
            <WorkspaceGroup
              key={group.workspace.path}
              group={group}
              isCurrent={group.workspace.path === currentWorkspacePath}
              currentSessionId={currentSessionId}
              runningSessionId={runningSessionId}
              onCreate={onCreate}
              onSwitch={onSwitch}
              onSwitchInWorkspace={onSwitchInWorkspace}
              onDelete={onDelete}
              onRemoveWorkspace={onRemoveWorkspace}
              onRename={onRename}
              onTogglePin={onTogglePin}
              onUpdateWorkspace={onUpdateWorkspace}
            />
          ))
        )}
      </div>
    </div>
  )
}

export default SessionList
