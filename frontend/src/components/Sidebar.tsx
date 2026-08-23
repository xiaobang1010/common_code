import { useState } from 'react'
import SessionList from './sidebar/SessionList'
import TaskGroupList, { GROUP_PALETTE } from './sidebar/TaskGroupList'
import type { SessionGroup, TaskGroupInfo } from '../api/client'

// 侧栏视图：项目（按工作区分组，现状）| 分组（自定义任务分组聚合）
export type SidebarView = 'projects' | 'groups'

// 入口区按钮统一样式（竖排：图标 + 文字居左，快捷键居右，无边框）
const entryButtonStyle: React.CSSProperties = {
  width: '100%',
  display: 'flex',
  alignItems: 'center',
  gap: '8px',
  padding: '7px 10px',
  border: 'none',
  borderRadius: 'var(--radius-sm)',
  background: 'transparent',
  color: 'var(--text-secondary)',
  fontSize: '12px',
  fontFamily: 'var(--font-ui)',
  cursor: 'pointer',
  transition: 'all var(--transition-fast)',
  textAlign: 'left',
}

// 快捷键提示统一样式（居右）
const entryShortcutStyle: React.CSSProperties = {
  marginLeft: 'auto',
  fontSize: '10px',
  fontFamily: 'var(--font-mono)',
  color: 'var(--text-tertiary)',
  letterSpacing: '0.3px',
}

// 视图切换 tab 统一样式（图标 + 文字；图标一律 SVG 绘制）
function tabStyle(active: boolean): React.CSSProperties {
  return {
    display: 'flex',
    alignItems: 'center',
    gap: '5px',
    padding: '4px 10px',
    border: 'none',
    borderRadius: 'var(--radius-sm)',
    background: active ? 'var(--selected-bg)' : 'transparent',
    color: active ? 'var(--text-primary)' : 'var(--text-secondary)',
    fontSize: '12px',
    fontFamily: 'var(--font-ui)',
    fontWeight: active ? 600 : 500,
    cursor: 'pointer',
    transition: 'all var(--transition-fast)',
  }
}

// 会话栏：左侧唯一的侧边区域，承载视图切换（分组/项目）与会话列表
// 文件视图在编辑区右缘树窄列、搜索/审查为编辑区工具标签，此处不再有视图切换
interface SidebarProps {
  collapsed: boolean
  onToggleCollapse: () => void
  // 会话列表相关 props
  groups: SessionGroup[]
  currentWorkspacePath: string | null
  currentSessionId: string | null
  // 当前运行任务所属会话（列表显示运行指示）
  runningSessionId: string | null
  // 视图切换（分组/项目），选中态由 App 持久化到 localStorage
  view: SidebarView
  onViewChange: (view: SidebarView) => void
  // 自定义任务分组（分组视图）
  taskGroups: TaskGroupInfo[]
  onCreateTaskGroup: (name: string, color?: string) => Promise<TaskGroupInfo | null>
  onRenameTaskGroup: (groupId: string, name: string) => void
  onDeleteTaskGroup: (groupId: string) => void
  onSetSessionGroup: (sessionId: string, groupId: string) => void
  onCreateSessionInGroup: (groupId: string) => void
  onCreateSession: () => void
  // 项目行「+」新建：在指定工作区建任务（非当前工作区先切换）
  onCreateSessionInWorkspace: (workspacePath: string) => void
  onSwitchSession: (sessionId: string) => void
  onSwitchInWorkspace: (sessionId: string, workspacePath: string) => void
  onDeleteSession: (sessionId: string) => void
  onRemoveWorkspace: (workspacePath: string) => void
  onOpenWorkspace: () => void
  // 打开搜索：打开搜索工具标签
  onOpenSearch: () => void
  onRenameSession: (sessionId: string, title: string) => void
  onToggleSessionPin: (sessionId: string, pinned: boolean) => void
  onUpdateWorkspace: (path: string, data: { alias?: string; pinned?: boolean }) => void
}

function Sidebar({ collapsed, onToggleCollapse, groups, currentWorkspacePath, currentSessionId, runningSessionId, view, onViewChange, taskGroups, onCreateTaskGroup, onRenameTaskGroup, onDeleteTaskGroup, onSetSessionGroup, onCreateSessionInGroup, onCreateSession, onCreateSessionInWorkspace, onSwitchSession, onSwitchInWorkspace, onDeleteSession, onRemoveWorkspace, onOpenWorkspace, onOpenSearch, onRenameSession, onToggleSessionPin, onUpdateWorkspace }: SidebarProps) {
  // 新建分组的内联输入态
  const [creatingGroup, setCreatingGroup] = useState(false)
  const [groupNameDraft, setGroupNameDraft] = useState('')

  const commitCreateGroup = async () => {
    const name = groupNameDraft.trim()
    if (name) {
      // 颜色按已有分组数自动从色板分配
      await onCreateTaskGroup(name, GROUP_PALETTE[taskGroups.length % GROUP_PALETTE.length])
    }
    setGroupNameDraft('')
    setCreatingGroup(false)
  }

  // 折叠状态下渲染一个窄条展开按钮
  if (collapsed) {
    return (
      <div
        style={{
          backgroundColor: 'var(--bg-base)',
          borderRight: '1px solid var(--border-subtle)',
          height: '100%',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          cursor: 'pointer',
          transition: 'background var(--transition-fast)',
        }}
        onClick={onToggleCollapse}
        title="展开会话栏"
        onMouseEnter={(e) => (e.currentTarget.style.background = 'var(--bg-tertiary)')}
        onMouseLeave={(e) => (e.currentTarget.style.background = 'var(--bg-base)')}
      >
        <span
          style={{
            color: 'var(--text-tertiary)',
            fontSize: '11px',
            writingMode: 'vertical-rl',
            letterSpacing: '1.5px',
            userSelect: 'none',
            fontFamily: 'var(--font-ui)',
            fontWeight: 500,
          }}
        >
          » 项目
        </span>
      </div>
    )
  }

  return (
    <div
      style={{
        backgroundColor: 'var(--bg-secondary)',
        borderRight: '1px solid var(--border)',
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        overflow: 'hidden',
      }}
    >
      {/* 顶部标题栏：品牌标识 + 标题 + 折叠按钮 */}
      <div
        style={{
          height: '44px',
          display: 'flex',
          alignItems: 'center',
          gap: '10px',
          padding: '0 8px 0 12px',
          borderBottom: '1px solid var(--border)',
          flexShrink: 0,
        }}
      >
        {/* 品牌标识（迁移自原活动栏）：单色字形小方块，无渐变无彩色 */}
        <div
          style={{
            width: '24px',
            height: '24px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            borderRadius: 'var(--radius-sm)',
            background: 'var(--bg-elevated)',
            border: '1px solid var(--border-strong)',
            color: 'var(--text-primary)',
            fontWeight: 700,
            fontSize: '12px',
            fontFamily: 'var(--font-display)',
            boxShadow: 'var(--shadow-sm)',
            letterSpacing: '-0.5px',
            flexShrink: 0,
          }}
          title="Common Code"
        >
          C
        </div>
        <span
          style={{
            fontSize: '11px',
            textTransform: 'uppercase',
            color: 'var(--text-secondary)',
            fontFamily: 'var(--font-ui)',
            fontWeight: 600,
            letterSpacing: '1.2px',
            flex: 1,
          }}
        >
          项目
        </span>
        <button
          onClick={onToggleCollapse}
          title="折叠会话栏"
          style={{
            border: 'none',
            background: 'transparent',
            color: 'var(--text-tertiary)',
            cursor: 'pointer',
            padding: '4px 6px',
            borderRadius: 'var(--radius-sm)',
            transition: 'all var(--transition-fast)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.background = 'var(--bg-tertiary)'
            e.currentTarget.style.color = 'var(--text-primary)'
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.background = 'transparent'
            e.currentTarget.style.color = 'var(--text-tertiary)'
          }}
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <path d="M15 6l-6 6 6 6" />
          </svg>
        </button>
      </div>
      {/* 常驻入口区：新建任务 / 搜索 / 打开工作区（竖排，分组折叠与否均可见） */}
      <div
        style={{
          display: 'flex',
          flexDirection: 'column',
          gap: '6px',
          padding: '8px',
          borderBottom: '1px solid var(--border)',
          flexShrink: 0,
        }}
      >
        <button
          onClick={onCreateSession}
          title="新建任务 (Ctrl+N)"
          style={entryButtonStyle}
          onMouseEnter={(e) => {
            e.currentTarget.style.color = 'var(--text-primary)'
            e.currentTarget.style.backgroundColor = 'var(--hover-bg)'
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.color = 'var(--text-secondary)'
            e.currentTarget.style.backgroundColor = 'transparent'
          }}
        >
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M12 5v14M5 12h14" />
          </svg>
          新建任务
          <span style={entryShortcutStyle}>Ctrl+N</span>
        </button>
        <button
          onClick={onOpenSearch}
          title="搜索 (Ctrl+K)"
          style={entryButtonStyle}
          onMouseEnter={(e) => {
            e.currentTarget.style.color = 'var(--text-primary)'
            e.currentTarget.style.backgroundColor = 'var(--hover-bg)'
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.color = 'var(--text-secondary)'
            e.currentTarget.style.backgroundColor = 'transparent'
          }}
        >
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="11" cy="11" r="7" />
            <path d="M21 21l-4.3-4.3" />
          </svg>
          搜索
          <span style={entryShortcutStyle}>Ctrl+K</span>
        </button>
        <button
          onClick={onOpenWorkspace}
          title="打开工作区"
          style={entryButtonStyle}
          onMouseEnter={(e) => {
            e.currentTarget.style.color = 'var(--text-primary)'
            e.currentTarget.style.backgroundColor = 'var(--hover-bg)'
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.color = 'var(--text-secondary)'
            e.currentTarget.style.backgroundColor = 'transparent'
          }}
        >
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z" />
          </svg>
          打开工作区
        </button>
      </div>
      {/* 视图切换 tab：「# 分组」/「项目」（文件夹图标），右侧 + 新建分组（仅分组视图） */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '4px',
          padding: '6px 8px',
          borderBottom: '1px solid var(--border)',
          flexShrink: 0,
        }}
      >
        <button
          onClick={() => onViewChange('groups')}
          title="按自定义分组查看任务"
          style={tabStyle(view === 'groups')}
          onMouseEnter={(e) => {
            if (view !== 'groups') e.currentTarget.style.color = 'var(--text-primary)'
          }}
          onMouseLeave={(e) => {
            if (view !== 'groups') e.currentTarget.style.color = 'var(--text-secondary)'
          }}
        >
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
            <path d="M4 9h16M4 15h16M10 3L8 21M16 3l-2 18" />
          </svg>
          分组
        </button>
        <button
          onClick={() => onViewChange('projects')}
          title="按工作区查看任务"
          style={tabStyle(view === 'projects')}
          onMouseEnter={(e) => {
            if (view !== 'projects') e.currentTarget.style.color = 'var(--text-primary)'
          }}
          onMouseLeave={(e) => {
            if (view !== 'projects') e.currentTarget.style.color = 'var(--text-secondary)'
          }}
        >
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z" />
          </svg>
          项目
        </button>
        {view === 'groups' && (
          <button
            onClick={() => setCreatingGroup(true)}
            title="新建分组"
            style={{
              marginLeft: 'auto',
              flexShrink: 0,
              padding: '4px 6px',
              border: 'none',
              background: 'transparent',
              color: creatingGroup ? 'var(--text-primary)' : 'var(--text-tertiary)',
              cursor: 'pointer',
              borderRadius: 'var(--radius-sm)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              transition: 'all var(--transition-fast)',
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.background = 'var(--hover-bg)'
              e.currentTarget.style.color = 'var(--text-primary)'
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.background = 'transparent'
              if (!creatingGroup) e.currentTarget.style.color = 'var(--text-tertiary)'
            }}
          >
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <path d="M12 5v14M5 12h14" />
            </svg>
          </button>
        )}
      </div>
      {/* 新建分组内联输入行 */}
      {creatingGroup && (
        <div style={{ padding: '6px 8px', borderBottom: '1px solid var(--border)', flexShrink: 0 }}>
          <input
            autoFocus
            value={groupNameDraft}
            onChange={(e) => setGroupNameDraft(e.target.value)}
            onBlur={commitCreateGroup}
            onKeyDown={(e) => {
              if (e.key === 'Enter') commitCreateGroup()
              if (e.key === 'Escape') {
                setGroupNameDraft('')
                setCreatingGroup(false)
              }
            }}
            placeholder="分组名称，Enter 创建，Esc 取消"
            style={{
              width: '100%',
              padding: '4px 8px',
              border: '1px solid var(--border-strong)',
              borderRadius: 'var(--radius-sm)',
              background: 'var(--bg-base)',
              color: 'var(--text-primary)',
              fontSize: '12px',
              fontFamily: 'var(--font-ui)',
              outline: 'none',
              boxShadow: '0 0 0 3px var(--focus-ring)',
              boxSizing: 'border-box',
            }}
          />
        </div>
      )}
      {/* 列表区：项目视图（工作区分组）或分组视图（自定义分组聚合） */}
      <div
        style={{
          flex: 1,
          overflow: 'hidden',
          display: 'flex',
          flexDirection: 'column',
        }}
      >
        {view === 'projects' ? (
          <SessionList
            groups={groups}
            currentWorkspacePath={currentWorkspacePath}
            currentSessionId={currentSessionId}
            runningSessionId={runningSessionId}
            onCreate={onCreateSessionInWorkspace}
            onSwitch={onSwitchSession}
            onSwitchInWorkspace={onSwitchInWorkspace}
            onDelete={onDeleteSession}
            onRemoveWorkspace={onRemoveWorkspace}
            onOpenWorkspace={onOpenWorkspace}
            onRename={onRenameSession}
            onTogglePin={onToggleSessionPin}
            onUpdateWorkspace={onUpdateWorkspace}
          />
        ) : (
          <TaskGroupList
            taskGroups={taskGroups}
            allGroups={groups}
            currentWorkspacePath={currentWorkspacePath}
            currentSessionId={currentSessionId}
            runningSessionId={runningSessionId}
            hasWorkspace={!!currentWorkspacePath}
            onCreateInGroup={onCreateSessionInGroup}
            onRenameGroup={onRenameTaskGroup}
            onDeleteGroup={onDeleteTaskGroup}
            onSetSessionGroup={onSetSessionGroup}
            onSwitchSession={onSwitchSession}
            onSwitchInWorkspace={onSwitchInWorkspace}
            onDeleteSession={onDeleteSession}
            onRenameSession={onRenameSession}
            onTogglePin={onToggleSessionPin}
          />
        )}
      </div>
    </div>
  )
}

export default Sidebar
