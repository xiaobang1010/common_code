import { useState } from 'react'
import type { SessionGroup, SessionInfo } from '../../api/client'

interface SessionListProps {
  groups: SessionGroup[]
  currentWorkspacePath: string | null
  currentSessionId: string | null
  onCreate: () => void
  onSwitch: (sessionId: string) => void
  onSwitchInWorkspace: (sessionId: string, workspacePath: string) => void
  onDelete: (sessionId: string) => void
  onRemoveWorkspace: (workspacePath: string) => void
  onOpenWorkspace: () => void
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

// 单个会话项，用自身 hover 状态控制删除按钮的显隐
function SessionItem({
  session,
  isActive,
  onSwitch,
  onDelete,
}: {
  session: SessionInfo
  isActive: boolean
  onSwitch: (id: string) => void
  onDelete: (id: string) => void
}) {
  const [hovered, setHovered] = useState(false)

  return (
    <div
      onClick={() => onSwitch(session.id)}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        position: 'relative',
        padding: '8px 10px 8px 14px',
        cursor: 'pointer',
        borderRadius: 'var(--radius-sm)',
        marginBottom: '2px',
        backgroundColor: isActive
          ? 'var(--accent-soft)'
          : hovered
            ? 'var(--bg-tertiary)'
            : 'transparent',
        transition: 'background var(--transition-fast)',
        // 当前会话左侧 accent 色边条
        borderLeft: isActive
          ? '3px solid var(--accent)'
          : '3px solid transparent',
      }}
    >
      {/* 标题行 */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <span
          style={{
            color: isActive ? 'var(--text-primary)' : 'var(--text-secondary)',
            fontSize: '12px',
            fontFamily: 'var(--font-ui)',
            fontWeight: 500,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
            flex: 1,
          }}
        >
          {session.title || '新对话'}
        </span>
        {/* 删除按钮 - 行悬停时显示 */}
        <button
          onClick={(e) => {
            e.stopPropagation()
            onDelete(session.id)
          }}
          title="删除会话"
          style={{
            flexShrink: 0,
            padding: '2px',
            border: 'none',
            background: 'transparent',
            color: 'var(--text-tertiary)',
            cursor: 'pointer',
            borderRadius: 'var(--radius-sm)',
            transition: 'all var(--transition-fast)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            marginLeft: '6px',
            opacity: hovered ? 1 : 0,
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.color = 'var(--error)'
            e.currentTarget.style.backgroundColor = 'rgba(255, 107, 107, 0.1)'
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.color = 'var(--text-tertiary)'
            e.currentTarget.style.backgroundColor = 'transparent'
          }}
        >
          {/* 垃圾桶图标 */}
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2m3 0v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6h14z" />
            <path d="M10 11v6M14 11v6" />
          </svg>
        </button>
      </div>

      {/* 底部信息行：更新时间 + 消息数 */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '8px',
          marginTop: '3px',
        }}
      >
        <span
          style={{
            color: 'var(--text-tertiary)',
            fontSize: '10px',
            fontFamily: 'var(--font-ui)',
          }}
        >
          {relativeTime(session.updated_at)}
        </span>
        {session.message_count > 0 && (
          <span
            style={{
              color: 'var(--text-tertiary)',
              fontSize: '10px',
              fontFamily: 'var(--font-ui)',
            }}
          >
            {session.message_count} 条消息
          </span>
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
  onCreate,
  onSwitch,
  onSwitchInWorkspace,
  onDelete,
  onRemoveWorkspace,
}: {
  group: SessionGroup
  isCurrent: boolean
  currentSessionId: string | null
  onCreate: () => void
  onSwitch: (sessionId: string) => void
  onSwitchInWorkspace: (sessionId: string, workspacePath: string) => void
  onDelete: (sessionId: string) => void
  onRemoveWorkspace: (workspacePath: string) => void
}) {
  // 当前工作区默认展开，其他默认折叠
  const [expanded, setExpanded] = useState(isCurrent)
  const [headerHovered, setHeaderHovered] = useState(false)
  const [menuOpen, setMenuOpen] = useState(false)

  return (
    <div style={{ marginBottom: '2px' }}>
      {/* 分组标题行 */}
      <div
        onClick={() => setExpanded(prev => !prev)}
        onMouseEnter={() => setHeaderHovered(true)}
        onMouseLeave={() => { setHeaderHovered(false); setMenuOpen(false) }}
        style={{
          display: 'flex',
          alignItems: 'center',
          padding: '6px 10px 6px 8px',
          cursor: 'pointer',
          borderRadius: 'var(--radius-sm)',
          backgroundColor: isCurrent
            ? 'var(--accent-soft)'
            : headerHovered
              ? 'var(--bg-tertiary)'
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
            color: isCurrent ? 'var(--accent)' : 'var(--text-tertiary)',
            transition: 'transform var(--transition-fast)',
            transform: expanded ? 'rotate(90deg)' : 'rotate(0deg)',
          }}
        >
          {'\u25B8'}
        </span>
        {/* 工作区名称 */}
        <span
          style={{
            flex: 1,
            fontSize: '12px',
            fontFamily: 'var(--font-ui)',
            fontWeight: 600,
            color: isCurrent ? 'var(--accent)' : 'var(--text-secondary)',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
          title={group.workspace.path}
        >
          {basename(group.workspace.path)}
        </span>
        {/* 会话数量 */}
        <span
          style={{
            fontSize: '10px',
            color: 'var(--text-tertiary)',
            fontFamily: 'var(--font-ui)',
            flexShrink: 0,
            marginLeft: '6px',
          }}
        >
          {group.sessions.length} 个会话
        </span>
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
            {/* 三个点图标 */}
            <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
              <circle cx="5" cy="12" r="1.5" />
              <circle cx="12" cy="12" r="1.5" />
              <circle cx="19" cy="12" r="1.5" />
            </svg>
          </button>
        )}
        {/* 下拉菜单 */}
        {menuOpen && (
          <div
            onClick={(e) => e.stopPropagation()}
            style={{
              position: 'absolute',
              top: '100%',
              right: '0',
              marginTop: '2px',
              backgroundColor: 'var(--bg-secondary)',
              border: '1px solid var(--border)',
              borderRadius: 'var(--radius-sm)',
              boxShadow: '0 4px 12px rgba(0, 0, 0, 0.3)',
              zIndex: 100,
              minWidth: '120px',
              overflow: 'hidden',
            }}
          >
            <button
              onClick={(e) => {
                e.stopPropagation()
                setMenuOpen(false)
                onRemoveWorkspace(group.workspace.path)
              }}
              style={{
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
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.backgroundColor = 'rgba(255, 107, 107, 0.1)'
                e.currentTarget.style.color = 'var(--error)'
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.backgroundColor = 'transparent'
                e.currentTarget.style.color = 'var(--text-secondary)'
              }}
            >
              {/* 垃圾桶图标 */}
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                <path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2m3 0v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6h14z" />
                <path d="M10 11v6M14 11v6" />
              </svg>
              移除工作区
            </button>
          </div>
        )}
      </div>

      {/* 展开内容 */}
      {expanded && (
        <div style={{ paddingLeft: '4px' }}>
          {/* 当前工作区显示新建任务按钮 */}
          {isCurrent && (
            <div style={{ padding: '6px 6px 6px 10px' }}>
              <button
                onClick={onCreate}
                style={{
                  width: '100%',
                  padding: '6px 10px',
                  border: '1px solid var(--border)',
                  borderRadius: 'var(--radius-md)',
                  backgroundColor: 'transparent',
                  color: 'var(--text-secondary)',
                  fontSize: '12px',
                  fontFamily: 'var(--font-ui)',
                  fontWeight: 500,
                  cursor: 'pointer',
                  transition: 'all var(--transition-fast)',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '6px',
                  justifyContent: 'center',
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.borderColor = 'var(--accent)'
                  e.currentTarget.style.color = 'var(--accent)'
                  e.currentTarget.style.backgroundColor = 'var(--accent-soft)'
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.borderColor = 'var(--border)'
                  e.currentTarget.style.color = 'var(--text-secondary)'
                  e.currentTarget.style.backgroundColor = 'transparent'
                }}
              >
                {/* + 图标 */}
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M12 5v14M5 12h14" />
                </svg>
                新建任务
              </button>
            </div>
          )}

          {/* 会话列表 */}
          {group.sessions.length === 0 ? (
            <div
              style={{
                padding: '12px 12px 12px 18px',
                color: 'var(--text-tertiary)',
                fontSize: '12px',
                fontFamily: 'var(--font-ui)',
              }}
            >
              暂无会话
            </div>
          ) : (
            group.sessions.map((session) => (
              <SessionItem
                key={session.id}
                session={session}
                isActive={session.id === currentSessionId}
                onSwitch={(id) => {
                  if (isCurrent) {
                    onSwitch(id)
                  } else {
                    onSwitchInWorkspace(id, group.workspace.path)
                  }
                }}
                onDelete={onDelete}
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
  onCreate,
  onSwitch,
  onSwitchInWorkspace,
  onDelete,
  onRemoveWorkspace,
  onOpenWorkspace,
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
            <button
              onClick={onOpenWorkspace}
              style={{
                padding: '8px 16px',
                border: '1px solid var(--accent)',
                borderRadius: 'var(--radius-md)',
                backgroundColor: 'var(--accent-soft)',
                color: 'var(--accent)',
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
                e.currentTarget.style.backgroundColor = 'var(--accent)'
                e.currentTarget.style.color = '#1a1a1a'
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.backgroundColor = 'var(--accent-soft)'
                e.currentTarget.style.color = 'var(--accent)'
              }}
            >
              {/* 文件夹打开图标 */}
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
              onCreate={onCreate}
              onSwitch={onSwitch}
              onSwitchInWorkspace={onSwitchInWorkspace}
              onDelete={onDelete}
              onRemoveWorkspace={onRemoveWorkspace}
            />
          ))
        )}
      </div>
    </div>
  )
}

export default SessionList
