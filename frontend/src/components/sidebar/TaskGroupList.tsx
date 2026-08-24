import { useState } from 'react'
import { SessionItem, sortSessions } from './SessionList'
import type { SessionGroup, SessionInfo, TaskGroupInfo } from '../../api/client'

// 固定 8 色色板：新建分组时按已有分组数自动分配，避免强制选色
export const GROUP_PALETTE = [
  '#e06c75', '#e5c07b', '#98c379', '#56b6c2',
  '#61afef', '#c678dd', '#d19a66', '#7f8c98',
]

interface TaskGroupListProps {
  taskGroups: TaskGroupInfo[]
  allGroups: SessionGroup[]
  currentWorkspacePath: string | null
  currentSessionId: string | null
  runningSessionIds: string[]
  hasWorkspace: boolean
  onCreateInGroup: (groupId: string) => void
  onRenameGroup: (groupId: string, name: string) => void
  onDeleteGroup: (groupId: string) => void
  onSetSessionGroup: (sessionId: string, groupId: string) => void
  onSwitchSession: (sessionId: string) => void
  onSwitchInWorkspace: (sessionId: string, workspacePath: string) => void
  onDeleteSession: (sessionId: string) => void
  onRenameSession: (sessionId: string, title: string) => void
  onTogglePin: (sessionId: string, pinned: boolean) => void
}

// 平铺所有工作区的任务（分组视图按自定义分组聚合，忽略工作区边界）
function flattenSessions(allGroups: SessionGroup[]): SessionInfo[] {
  return allGroups.flatMap((g) => g.sessions)
}

// 菜单项统一样式（与 SessionList 保持一致）
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

// 折叠箭头（展开旋转 90°）
function Chevron({ expanded }: { expanded: boolean }) {
  return (
    <span
      style={{
        display: 'inline-block',
        width: '14px',
        fontSize: '10px',
        color: 'var(--text-tertiary)',
        transition: 'transform var(--transition-fast)',
        transform: expanded ? 'rotate(90deg)' : 'rotate(0deg)',
        flexShrink: 0,
      }}
    >
      {'\u25B8'}
    </span>
  )
}

// 分区容器：折叠头行 + 内容；整个分区是拖放落区（悬停高亮反馈）
function DropSection({
  expanded,
  onToggle,
  onDropMove,
  header,
  children,
}: {
  expanded: boolean
  onToggle: () => void
  // payload 为拖拽的 session id；null/空表示拖拽里没有有效数据
  onDropMove: (payload: string | null) => void
  header: React.ReactNode
  children: React.ReactNode
}) {
  const [dropActive, setDropActive] = useState(false)

  return (
    <div
      style={{
        marginBottom: '2px',
        borderRadius: 'var(--radius-sm)',
        background: dropActive ? 'var(--hover-bg)' : 'transparent',
        boxShadow: dropActive ? 'inset 0 0 0 1px var(--border-strong)' : 'none',
        transition: 'background var(--transition-fast)',
      }}
      onDragOver={(e) => {
        e.preventDefault()
        e.dataTransfer.dropEffect = 'move'
        setDropActive(true)
      }}
      onDragLeave={(e) => {
        // 只在真正离开分区边界时撤高亮（进出子元素也会触发 dragleave）
        if (!e.currentTarget.contains(e.relatedTarget as Node)) setDropActive(false)
      }}
      onDrop={(e) => {
        e.preventDefault()
        setDropActive(false)
        const payload = e.dataTransfer.getData('text/session-id')
        onDropMove(payload || null)
      }}
    >
      {/* 头部行：点击切换折叠 */}
      <div
        onClick={onToggle}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '4px',
          padding: '6px 10px 6px 8px',
          cursor: 'pointer',
          borderRadius: 'var(--radius-sm)',
          userSelect: 'none',
          position: 'relative',
        }}
        onMouseEnter={(e) => {
          if (!dropActive) e.currentTarget.style.backgroundColor = 'var(--hover-bg)'
        }}
        onMouseLeave={(e) => {
          e.currentTarget.style.backgroundColor = 'transparent'
        }}
      >
        {header}
      </div>
      {/* 展开内容 */}
      {expanded && <div style={{ paddingLeft: '6px' }}>{children}</div>}
    </div>
  )
}

// 头部小图标按钮通用样式（常显低强调，悬停提亮）
const headIconButtonStyle: React.CSSProperties = {
  flexShrink: 0,
  padding: '2px 4px',
  marginLeft: '4px',
  border: 'none',
  background: 'transparent',
  color: 'var(--text-tertiary)',
  cursor: 'pointer',
  borderRadius: 'var(--radius-sm)',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  opacity: 0.65,
  transition: 'color var(--transition-fast)',
}

// 单个自定义分组：色点 + 名称 + 数量 + ⊕ + 菜单（重命名/删除）
function CustomGroupSection({
  group,
  sessions,
  hasWorkspace,
  currentSessionId,
  runningSessionIds,
  currentWorkspacePath,
  onCreateInGroup,
  onRenameGroup,
  onDeleteGroup,
  onSetSessionGroup,
  onSwitchSession,
  onSwitchInWorkspace,
  onDeleteSession,
  onRenameSession,
  onTogglePin,
}: {
  group: TaskGroupInfo
  sessions: SessionInfo[]
} & Pick<
  TaskGroupListProps,
  | 'hasWorkspace'
  | 'currentSessionId'
  | 'runningSessionIds'
  | 'currentWorkspacePath'
  | 'onCreateInGroup'
  | 'onRenameGroup'
  | 'onDeleteGroup'
  | 'onSetSessionGroup'
  | 'onSwitchSession'
  | 'onSwitchInWorkspace'
  | 'onDeleteSession'
  | 'onRenameSession'
  | 'onTogglePin'
>) {
  const [expanded, setExpanded] = useState(true)
  const [menuOpen, setMenuOpen] = useState(false)
  const [renameEditing, setRenameEditing] = useState(false)
  const [renameDraft, setRenameDraft] = useState(group.name)

  const commitRename = () => {
    const trimmed = renameDraft.trim()
    if (trimmed && trimmed !== group.name) onRenameGroup(group.id, trimmed)
    setRenameEditing(false)
  }

  return (
    <DropSection
      expanded={expanded}
      onToggle={() => setExpanded((prev) => !prev)}
      onDropMove={(payload) => {
        if (payload) onSetSessionGroup(payload, group.id)
      }}
      header={
        <>
          <Chevron expanded={expanded} />
          {/* 色点：无 color 时回退中性色 */}
          <span
            style={{
              width: '8px',
              height: '8px',
              borderRadius: '50%',
              background: group.color || 'var(--text-tertiary)',
              flexShrink: 0,
            }}
          />
          {renameEditing ? (
            <input
              autoFocus
              value={renameDraft}
              onClick={(e) => e.stopPropagation()}
              onChange={(e) => setRenameDraft(e.target.value)}
              onBlur={commitRename}
              onKeyDown={(e) => {
                if (e.key === 'Enter') commitRename()
                if (e.key === 'Escape') setRenameEditing(false)
              }}
              style={{
                flex: 1,
                minWidth: 0,
                padding: '2px 6px',
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
          ) : (
            <span
              style={{
                flex: 1,
                fontSize: '12px',
                fontFamily: 'var(--font-ui)',
                fontWeight: 600,
                color: 'var(--text-secondary)',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
                minWidth: 0,
              }}
              title={group.name}
            >
              {group.name}
            </span>
          )}
          {/* 任务数量 */}
          <span
            style={{
              fontSize: '10px',
              color: 'var(--text-tertiary)',
              fontFamily: 'var(--font-ui)',
              flexShrink: 0,
              marginLeft: '6px',
            }}
          >
            {sessions.length}
          </span>
          {/* 组内新建任务：无工作区时置灰提示先打开工作区 */}
          <button
            onClick={(e) => {
              e.stopPropagation()
              if (hasWorkspace) onCreateInGroup(group.id)
            }}
            disabled={!hasWorkspace}
            title={hasWorkspace ? '在此分组新建任务' : '请先打开工作区'}
            style={{ ...headIconButtonStyle, cursor: hasWorkspace ? 'pointer' : 'not-allowed', opacity: hasWorkspace ? 0.65 : 0.3 }}
            onMouseEnter={(e) => {
              if (hasWorkspace) e.currentTarget.style.opacity = '1'
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.opacity = hasWorkspace ? '0.65' : '0.3'
            }}
          >
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <path d="M12 5v14M5 12h14" />
            </svg>
          </button>
          {/* 菜单按钮：重命名 / 删除分组 */}
          <button
            onClick={(e) => {
              e.stopPropagation()
              setMenuOpen((prev) => !prev)
            }}
            title="更多操作"
            style={{
              ...headIconButtonStyle,
              background: menuOpen ? 'var(--bg-tertiary)' : 'transparent',
            }}
            onMouseEnter={(e) => (e.currentTarget.style.opacity = '1')}
            onMouseLeave={(e) => (e.currentTarget.style.opacity = '0.65')}
          >
            <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor">
              <circle cx="5" cy="12" r="1.5" />
              <circle cx="12" cy="12" r="1.5" />
              <circle cx="19" cy="12" r="1.5" />
            </svg>
          </button>
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
                onClick={() => {
                  setMenuOpen(false)
                  setRenameDraft(group.name)
                  setRenameEditing(true)
                }}
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
                onClick={() => {
                  setMenuOpen(false)
                  onDeleteGroup(group.id)
                }}
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
                删除分组
              </button>
            </div>
          )}
        </>
      }
    >
      {/* 组内任务 / 空态落区提示 */}
      {sessions.length === 0 ? (
        <div
          onClick={() => hasWorkspace && onCreateInGroup(group.id)}
          title={hasWorkspace ? '新建任务' : '请先打开工作区'}
          style={{
            margin: '0 6px 6px 10px',
            padding: '10px 12px',
            border: '1px dashed var(--border-strong)',
            borderRadius: 'var(--radius-md)',
            color: 'var(--text-tertiary)',
            fontSize: '12px',
            fontFamily: 'var(--font-ui)',
            textAlign: 'center',
            cursor: hasWorkspace ? 'pointer' : 'default',
          }}
        >
          新建任务，或拖拽到这里。
        </div>
      ) : (
        sortSessions(sessions).map((session) => (
          <SessionItem
            key={session.id}
            session={session}
            isActive={session.id === currentSessionId}
            isRunning={runningSessionIds.includes(session.id)}
            enableDrag
            onSwitch={(id) => {
              if (session.workspace_path === currentWorkspacePath) onSwitchSession(id)
              else onSwitchInWorkspace(id, session.workspace_path)
            }}
            onDelete={onDeleteSession}
            onRename={onRenameSession}
            onTogglePin={onTogglePin}
          />
        ))
      )}
    </DropSection>
  )
}

// 分组视图：顶部未分区（跨工作区平铺）+ 各自定义分组
function TaskGroupList({
  taskGroups,
  allGroups,
  currentWorkspacePath,
  currentSessionId,
  runningSessionIds,
  hasWorkspace,
  onCreateInGroup,
  onRenameGroup,
  onDeleteGroup,
  onSetSessionGroup,
  onSwitchSession,
  onSwitchInWorkspace,
  onDeleteSession,
  onRenameSession,
  onTogglePin,
}: TaskGroupListProps) {
  const allSessions = flattenSessions(allGroups)
  const ungrouped = allSessions.filter((s) => !s.group_id)
  const [ungroupedExpanded, setUngroupedExpanded] = useState(true)

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        overflowY: 'auto',
        padding: '6px 4px',
      }}
    >
      {/* 未分区：所有 group_id 为空的任务；拖入 = 移出分组 */}
      <DropSection
        expanded={ungroupedExpanded}
        onToggle={() => setUngroupedExpanded((prev) => !prev)}
        onDropMove={(payload) => {
          if (payload) onSetSessionGroup(payload, '')
        }}
        header={
          <>
            <Chevron expanded={ungroupedExpanded} />
            <span
              style={{
                flex: 1,
                fontSize: '12px',
                fontFamily: 'var(--font-ui)',
                fontWeight: 600,
                color: 'var(--text-secondary)',
              }}
            >
              未分组
            </span>
            <span
              style={{
                fontSize: '10px',
                color: 'var(--text-tertiary)',
                fontFamily: 'var(--font-ui)',
                flexShrink: 0,
                marginLeft: '6px',
              }}
            >
              {ungrouped.length}
            </span>
          </>
        }
      >
        {ungrouped.length === 0 ? (
          <div
            style={{
              padding: '8px 12px 8px 16px',
              color: 'var(--text-tertiary)',
              fontSize: '12px',
              fontFamily: 'var(--font-ui)',
            }}
          >
            暂无未分组任务，可把任务拖到这里移出分组。
          </div>
        ) : (
          sortSessions(ungrouped).map((session) => (
            <SessionItem
              key={session.id}
              session={session}
              isActive={session.id === currentSessionId}
              isRunning={runningSessionIds.includes(session.id)}
              enableDrag
              onSwitch={(id) => {
                if (session.workspace_path === currentWorkspacePath) onSwitchSession(id)
                else onSwitchInWorkspace(id, session.workspace_path)
              }}
              onDelete={onDeleteSession}
              onRename={onRenameSession}
              onTogglePin={onTogglePin}
            />
          ))
        )}
      </DropSection>

      {/* 自定义分组列表（后端按 created_at 升序返回） */}
      {taskGroups.map((group) => (
        <CustomGroupSection
          key={group.id}
          group={group}
          sessions={allSessions.filter((s) => s.group_id === group.id)}
          hasWorkspace={hasWorkspace}
          currentSessionId={currentSessionId}
          runningSessionIds={runningSessionIds}
          currentWorkspacePath={currentWorkspacePath}
          onCreateInGroup={onCreateInGroup}
          onRenameGroup={onRenameGroup}
          onDeleteGroup={onDeleteGroup}
          onSetSessionGroup={onSetSessionGroup}
          onSwitchSession={onSwitchSession}
          onSwitchInWorkspace={onSwitchInWorkspace}
          onDeleteSession={onDeleteSession}
          onRenameSession={onRenameSession}
          onTogglePin={onTogglePin}
        />
      ))}

      {/* 无任何自定义分组时的引导 */}
      {taskGroups.length === 0 && (
        <div
          style={{
            padding: '12px 10px',
            color: 'var(--text-tertiary)',
            fontSize: '12px',
            fontFamily: 'var(--font-ui)',
            lineHeight: 1.7,
          }}
        >
          还没有自定义分组。点击右上角「+」创建一个，把相关任务拖进去归类。
        </div>
      )}
    </div>
  )
}

export default TaskGroupList
