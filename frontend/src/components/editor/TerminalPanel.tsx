import { useCallback, useEffect, useState } from 'react'
import Terminal from './Terminal'
import Resizer from '../Resizer'

// 终端会话信息
interface TerminalTab {
  id: string       // 前端分配的实例 id
  title: string    // 显示名称（pty 就绪后回填 shell 名，如 powershell）
  ptyId?: string   // 后端 pty id，创建后填充
}

// 单个工作区的终端组：会话列表 + 当前激活会话。
// 各工作区的终端互不干扰：切到别的工作区时整组只隐藏不卸载，里面跑着的命令继续
interface WorkspaceGroup {
  tabs: TerminalTab[]
  activeId: string
}

// 终端图标：命令行提示符。标题栏开关与面板头共用，图标随终端功能走
export const TerminalIcon = (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
    <path d="M4 17l6-5-6-5" />
    <path d="M12 19h8" />
  </svg>
)

// 生成唯一 id
const genId = () => `term-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`

// 新建一个含单会话的终端组
const newGroup = (): WorkspaceGroup => {
  const tab: TerminalTab = { id: genId(), title: 'TERMINAL' }
  return { tabs: [tab], activeId: tab.id }
}

// 工作区分组键：未选工作区时单独成组（shell 落在主进程兜底的目录）
const keyOf = (workspacePath: string | null) => workspacePath ?? ''

interface TerminalPanelProps {
  // 展开/收起：收起只切 display，不卸载组件（会话保活）
  open: boolean
  // 展开态高度（px），由 App 按上下限钳制
  height: number
  // 顶部拖拽回调，传入鼠标位移，由 App 决定高度
  onResize: (deltaPx: number) => void
  // 收起面板（与标题栏终端开关同一动作）
  onClose: () => void
  // 当前工作区目录：切换工作区即切换终端分组
  workspacePath: string | null
}

// 会话区底部终端面板：顶部可拖拽分隔条 + 面板头（标题 / 会话条 / 新建 / 收起）+ 终端内容区。
// 终端按工作区分组：每组各有会话列表与自己的 shell，面板头只呈现当前工作区那一组
function TerminalPanel({ open, height, onResize, onClose, workspacePath }: TerminalPanelProps) {
  const workspaceKey = keyOf(workspacePath)
  // 面板首次展开时给当前工作区建组，之后每访问到一个新工作区各建一组。
  // 面板未展开期间切工作区不建组：不在没人用的工作区里白起 shell
  const [groups, setGroups] = useState<Record<string, WorkspaceGroup>>(() => ({
    [workspaceKey]: newGroup(),
  }))

  useEffect(() => {
    if (!open) return
    setGroups((prev) => (prev[workspaceKey] ? prev : { ...prev, [workspaceKey]: newGroup() }))
  }, [open, workspaceKey])

  const addTerminal = useCallback((path: string) => {
    setGroups((prev) => {
      const g = prev[path]
      if (!g) return prev
      const tab: TerminalTab = { id: genId(), title: 'TERMINAL' }
      return { ...prev, [path]: { tabs: [...g.tabs, tab], activeId: tab.id } }
    })
  }, [])

  const closeTerminal = useCallback((path: string, id: string) => {
    setGroups((prev) => {
      const g = prev[path]
      if (!g) return prev
      const next = g.tabs.filter((t) => t.id !== id)
      if (next.length === 0) {
        // 至少保留一个会话
        return { ...prev, [path]: newGroup() }
      }
      // 关掉的是当前会话时，激活态落到剩下的最后一个
      const activeId = id === g.activeId ? next[next.length - 1].id : g.activeId
      return { ...prev, [path]: { tabs: next, activeId } }
    })
  }, [])

  const activateTerminal = useCallback((path: string, id: string) => {
    setGroups((prev) => {
      const g = prev[path]
      if (!g) return prev
      return { ...prev, [path]: { tabs: g.tabs, activeId: id } }
    })
  }, [])

  // pty 就绪：记录 ptyId 并把标签标题回填为实际 shell 名（如 powershell），替代固定 TERMINAL
  const handleReady = useCallback((path: string, tabId: string, ptyId: string, shell: string) => {
    setGroups((prev) => {
      const g = prev[path]
      if (!g) return prev
      return {
        ...prev,
        [path]: {
          ...g,
          tabs: g.tabs.map((t) =>
            t.id === tabId ? { ...t, ptyId, title: shell.replace(/\.exe$/i, '') } : t
          ),
        },
      }
    })
  }, [])

  const active = groups[workspaceKey]

  return (
    <div
      style={{
        // 收起只隐藏：终端组件留在树上，pty 与其输出都不受影响
        display: open ? 'flex' : 'none',
        flexDirection: 'column',
        height: `${height}px`,
        // 纵向空间不足时由上方对话区承担收缩，不压扁终端
        flexShrink: 0,
        minHeight: 0,
        backgroundColor: 'var(--bg-base)',
        borderTop: '1px solid var(--border)',
        overflow: 'hidden',
      }}
    >
      {/* 顶部分隔条：向上拖面板变高（方向语义由 App 的钳制逻辑决定） */}
      <Resizer direction="vertical" onResize={onResize} />

      {/* 面板头：标题 + 当前工作区的会话条 + 新建 + 收起 */}
      <div
        style={{
          height: '32px',
          display: 'flex',
          alignItems: 'stretch',
          backgroundColor: 'var(--bg-base)',
          borderBottom: '1px solid var(--border-subtle)',
          flexShrink: 0,
        }}
      >
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '6px',
            padding: '0 12px',
            color: 'var(--text-secondary)',
            fontSize: '11px',
            fontFamily: 'var(--font-ui)',
            letterSpacing: '0.5px',
            flexShrink: 0,
          }}
        >
          <span style={{ display: 'flex', alignItems: 'center' }}>{TerminalIcon}</span>
          <span>终端</span>
        </div>

        {/* 会话条：空间不足时本组可横向滚动，收起按钮始终可见 */}
        <div
          style={{
            display: 'flex',
            alignItems: 'stretch',
            borderLeft: '1px solid var(--border-subtle)',
            flex: '0 1 auto',
            minWidth: 0,
            overflowX: 'auto',
            overflowY: 'hidden',
          }}
        >
          {(active?.tabs ?? []).map((tab, idx) => {
            const isActive = tab.id === active?.activeId
            return (
              <div
                key={tab.id}
                onClick={() => activateTerminal(workspaceKey, tab.id)}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '6px',
                  padding: '0 12px',
                  cursor: 'pointer',
                  fontSize: '11px',
                  fontFamily: 'var(--font-mono)',
                  color: isActive ? 'var(--text-primary)' : 'var(--text-tertiary)',
                  backgroundColor: isActive ? 'var(--bg-primary)' : 'transparent',
                  borderRight: '1px solid var(--border-subtle)',
                  borderBottom: isActive ? '2px solid var(--accent)' : '2px solid transparent',
                  whiteSpace: 'nowrap',
                  flexShrink: 0,
                  transition: 'all var(--transition-fast)',
                  letterSpacing: '0.5px',
                }}
                onMouseEnter={(e) => {
                  if (!isActive) {
                    e.currentTarget.style.color = 'var(--text-secondary)'
                    e.currentTarget.style.backgroundColor = 'var(--bg-tertiary)'
                  }
                }}
                onMouseLeave={(e) => {
                  if (!isActive) {
                    e.currentTarget.style.color = 'var(--text-tertiary)'
                    e.currentTarget.style.backgroundColor = 'transparent'
                  }
                }}
              >
                <span>{tab.title} {idx + 1}</span>
                {/* 关闭按钮 - 多于 1 个会话才显示 */}
                {(active?.tabs.length ?? 0) > 1 && (
                  <button
                    onClick={(e) => {
                      e.stopPropagation()
                      closeTerminal(workspaceKey, tab.id)
                    }}
                    title="关闭终端会话"
                    style={{
                      border: 'none',
                      background: 'transparent',
                      color: 'var(--text-tertiary)',
                      cursor: 'pointer',
                      padding: '0',
                      width: '14px',
                      height: '14px',
                      borderRadius: '3px',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      transition: 'all var(--transition-fast)',
                    }}
                    onMouseEnter={(e) => {
                      e.currentTarget.style.background = 'var(--bg-elevated)'
                      e.currentTarget.style.color = 'var(--error)'
                    }}
                    onMouseLeave={(e) => {
                      e.currentTarget.style.background = 'transparent'
                      e.currentTarget.style.color = 'var(--text-tertiary)'
                    }}
                  >
                    ×
                  </button>
                )}
              </div>
            )
          })}
          {/* 新建终端会话 */}
          <button
            onClick={() => addTerminal(workspaceKey)}
            title="新建终端"
            style={{
              border: 'none',
              background: 'transparent',
              color: 'var(--text-tertiary)',
              cursor: 'pointer',
              padding: '0 10px',
              flexShrink: 0,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              transition: 'all var(--transition-fast)',
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
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M12 5v14M5 12h14" />
            </svg>
          </button>
        </div>

        {/* 右端收起：与标题栏终端开关等价 */}
        <button
          onClick={onClose}
          title="收起终端面板"
          style={{
            marginLeft: 'auto',
            border: 'none',
            background: 'transparent',
            color: 'var(--text-tertiary)',
            cursor: 'pointer',
            padding: '0 12px',
            flexShrink: 0,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontSize: '15px',
            lineHeight: 1,
            transition: 'all var(--transition-fast)',
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
          ×
        </button>
      </div>

      {/* 内容区：每个工作区的会话组各自挂载，非当前工作区只隐藏（终端会话保活）。
          只渲染当前激活会话，切换会话重建该组的终端 */}
      <div style={{ flex: 1, minHeight: 0, overflow: 'hidden', position: 'relative' }}>
        {Object.entries(groups).map(([path, group]) => (
          <div
            key={path}
            style={{
              position: 'absolute',
              inset: 0,
              display: path === workspaceKey ? 'flex' : 'none',
              flexDirection: 'column',
            }}
          >
            <Terminal
              key={group.activeId}
              instanceId={group.activeId}
              cwd={path || undefined}
              onReady={(ptyId, shell) => handleReady(path, group.activeId, ptyId, shell)}
            />
            {/* 弱提示：会话未就绪/尚无输出时非纯空白，不抢焦点不打断 */}
            {!group.tabs.find((t) => t.id === group.activeId)?.ptyId && (
              <div
                style={{
                  position: 'absolute',
                  inset: 0,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  color: 'var(--text-tertiary)',
                  fontSize: '12px',
                  fontFamily: 'var(--font-ui)',
                  pointerEvents: 'none',
                }}
              >
                暂无终端输出
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

export default TerminalPanel
