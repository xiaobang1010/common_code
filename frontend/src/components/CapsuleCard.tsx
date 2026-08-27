import { useEffect, useRef, useState } from 'react'
import { TOOL_META, type ToolId } from './editor/toolMeta'
import { useGitStatus } from './inspector/useGitStatus'
import { useRunningSubagents, type RunningSubagent } from '../hooks/useRunningSubagents'
import { useChatStore } from '../stores/useChatStore'

interface CapsuleCardProps {
  // 点区块直达对应工具标签：展开面板并激活该标签
  onOpenTool: (id: ToolId) => void
  // 当前任务是否在跑（会话运行中），驱动进展区块呼吸灯
  isTaskRunning: boolean
  // 当前会话 id，供智能体轮询按会话过滤
  sessionId: string | null
}

// ··· 菜单展示的工具标签：概要/终端/审查。
// 「文件」入口已迁至侧栏工作区行、「搜索」有侧栏常驻按钮与 Ctrl+K，不再重复列出
const MENU_TOOL_IDS: ToolId[] = ['summary', 'terminal', 'review']

// 区块统一样式：整行可点的分组行，hover 浮起
function sectionStyle(hovered: boolean): React.CSSProperties {
  return {
    display: 'flex',
    alignItems: 'center',
    gap: '8px',
    padding: '5px 8px',
    margin: '0 -4px',
    borderRadius: 'var(--radius-sm)',
    background: hovered ? 'var(--bg-base)' : 'transparent',
    cursor: 'pointer',
    transition: 'all var(--transition-fast)',
    userSelect: 'none',
  }
}

// 区块文字：小号字，dim 为弱化灰
function sectionLabelStyle(dim: boolean): React.CSSProperties {
  return {
    fontSize: '11px',
    fontFamily: 'var(--font-ui)',
    fontWeight: 500,
    color: dim ? 'var(--text-tertiary)' : 'var(--text-secondary)',
    whiteSpace: 'nowrap',
  }
}

// 状态点：复用标题栏呼吸灯语义（运行中蓝脉冲，就绪绿常亮）
function RunDot({ active }: { active: boolean }) {
  return (
    <span
      style={{
        width: '7px',
        height: '7px',
        borderRadius: '50%',
        flexShrink: 0,
        backgroundColor: active ? 'var(--info)' : 'var(--success)',
        boxShadow: active ? 'var(--info-glow)' : 'var(--success-glow)',
        animation: active ? 'breathe 1.4s ease-in-out infinite' : 'none',
      }}
    />
  )
}

// 跳转消息流对应 SubagentCard：锚点命中即滚过去并短暂高亮；
// 目标块因「运行中仅渲染最近 3 步骤」未含卡片时，回退滚到运行中的工作块
function jumpToSubagent(agentId: string) {
  // 复用 SubagentCard 根节点现有 data-agent-id（其 agentId 已含结果解析与列表回填两种来源）
  const card = document.querySelector(`[data-agent-id="${CSS.escape(agentId)}"]`)
  const target = card ?? document.querySelector('[data-workblock-running="true"]')
  if (!target) return
  target.scrollIntoView({ behavior: 'smooth', block: 'center' })
  const el = target as HTMLElement
  el.style.transition = 'box-shadow 0.3s ease'
  el.style.boxShadow = '0 0 0 2px var(--info)'
  setTimeout(() => {
    el.style.boxShadow = ''
  }, 1200)
}

// 状态胶囊卡：替代原 IconRail 图标轨。常驻「进展」「产物」两块可快速审查的
// 信息；「智能体」未常驻区块仅运行时出现；卡头 ··· 菜单兜底手动打开全部
// 工具标签。仍只在编辑区折叠时由 App 渲染（fixed 右上，不与面板重叠）。
function CapsuleCard({ onOpenTool, isTaskRunning, sessionId }: CapsuleCardProps) {
  const git = useGitStatus()
  const { running: runningAgents } = useRunningSubagents(sessionId)
  const blockCount = useChatStore((s) => s.blockIds.length)
  const [menuOpen, setMenuOpen] = useState(false)
  const [agentsOpen, setAgentsOpen] = useState(false)
  const menuRef = useRef<HTMLDivElement>(null)

  // ··· 菜单点外部关闭
  useEffect(() => {
    if (!menuOpen) return
    const onDown = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuOpen(false)
      }
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [menuOpen])

  const totals = git.data?.totals ?? { files: 0, additions: 0, deletions: 0 }

  const jumpAgent = (a: RunningSubagent) => {
    setAgentsOpen(false)
    jumpToSubagent(a.agent_id)
  }

  return (
    <div
      style={{
        position: 'fixed',
        top: '44px', // 标题栏 38px 下方留出间距，避免遮挡标题栏交互区
        right: '14px',
        display: 'flex',
        flexDirection: 'column',
        gap: '2px',
        padding: '8px',
        borderRadius: 'var(--radius-lg)',
        backgroundColor: 'var(--bg-elevated)',
        boxShadow: 'var(--shadow-md)',
        border: '1px solid var(--border-subtle)',
        zIndex: 40,
        userSelect: 'none',
      }}
    >
      {/* 卡头：标题 + ··· 工具标签菜单 */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '8px', marginBottom: '2px' }}>
        <span style={sectionLabelStyle(true)}>状态</span>
        <div ref={menuRef} style={{ position: 'relative' }}>
          <button
            onClick={() => setMenuOpen((v) => !v)}
            title="打开工具面板"
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              width: '20px',
              height: '20px',
              border: 'none',
              borderRadius: 'var(--radius-sm)',
              background: menuOpen ? 'var(--bg-base)' : 'transparent',
              color: 'var(--text-tertiary)',
              cursor: 'pointer',
              fontSize: '12px',
              lineHeight: 1,
              letterSpacing: '1px',
              transition: 'all var(--transition-fast)',
              padding: 0,
            }}
          >
            ···
          </button>
          {menuOpen && (
            <div
              style={{
                position: 'absolute',
                right: 0,
                top: '24px',
                display: 'flex',
                flexDirection: 'column',
                minWidth: '88px',
                padding: '4px',
                borderRadius: 'var(--radius-md)',
                backgroundColor: 'var(--bg-elevated)',
                border: '1px solid var(--border)',
                boxShadow: 'var(--shadow-md)',
                zIndex: 50,
              }}
            >
              {/* 只列胶囊卡没有专属区块承载的入口之外仍有意义的标签：
                  「文件」入口在侧栏工作区行、「搜索」在侧栏常驻按钮与 Ctrl+K，不在此重复 */}
              {TOOL_META.filter(({ id }) => MENU_TOOL_IDS.includes(id)).map(({ id, title }) => (
                <button
                  key={id}
                  onClick={() => {
                    setMenuOpen(false)
                    onOpenTool(id)
                  }}
                  style={{
                    display: 'block',
                    width: '100%',
                    textAlign: 'left',
                    border: 'none',
                    borderRadius: 'var(--radius-sm)',
                    background: 'transparent',
                    color: 'var(--text-secondary)',
                    fontSize: '11px',
                    fontFamily: 'var(--font-ui)',
                    padding: '5px 8px',
                    cursor: 'pointer',
                  }}
                  onMouseEnter={(e) => {
                    e.currentTarget.style.background = 'var(--bg-base)'
                    e.currentTarget.style.color = 'var(--text-primary)'
                  }}
                  onMouseLeave={(e) => {
                    e.currentTarget.style.background = 'transparent'
                    e.currentTarget.style.color = 'var(--text-secondary)'
                  }}
                >
                  {title}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* 进展（常驻）：运行态 + 工作块数，点击跳概要 */}
      <div style={sectionStyle(false)} onClick={() => onOpenTool('summary')} title="查看概要">
        <RunDot active={isTaskRunning} />
        <span style={sectionLabelStyle(false)}>{isTaskRunning ? '运行中' : '就绪'}</span>
        <span style={{ ...sectionLabelStyle(true), marginLeft: 'auto' }}>{blockCount} 个工作块</span>
      </div>

      {/* 产物（常驻）：git 变更数，点击跳审查 */}
      <div style={sectionStyle(false)} onClick={() => onOpenTool('review')} title="查看审查详情">
        <span style={sectionLabelStyle(false)}>产物</span>
        {totals.files > 0 ? (
          <span style={{ ...sectionLabelStyle(false), marginLeft: 'auto', display: 'flex', gap: '6px' }}>
            <span>{totals.files} 文件</span>
            <span style={{ color: 'var(--success)' }}>+{totals.additions}</span>
            <span style={{ color: 'var(--error)' }}>−{totals.deletions}</span>
          </span>
        ) : (
          <span style={{ ...sectionLabelStyle(true), marginLeft: 'auto' }}>无变更</span>
        )}
      </div>

      {/* 智能体（未常驻）：仅运行中子代理存在时出现，点击条目跳消息流 */}
      {runningAgents.length > 0 && (
        <div>
          <div style={sectionStyle(false)} onClick={() => setAgentsOpen((v) => !v)} title="运行中的智能体">
            <RunDot active />
            <span style={sectionLabelStyle(false)}>智能体</span>
            <span style={{ ...sectionLabelStyle(true), marginLeft: 'auto' }}>{runningAgents.length} 运行中</span>
          </div>
          {agentsOpen &&
            runningAgents.map((a) => (
              <div
                key={a.agent_id}
                style={{ ...sectionStyle(false), paddingLeft: '20px' }}
                onClick={() => jumpAgent(a)}
                title={a.description}
              >
                <span
                  style={{
                    ...sectionLabelStyle(false),
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                    maxWidth: '150px',
                  }}
                >
                  {a.description}
                </span>
              </div>
            ))}
        </div>
      )}
    </div>
  )
}

export default CapsuleCard
