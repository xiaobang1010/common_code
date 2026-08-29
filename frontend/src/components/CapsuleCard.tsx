import { useEffect, useRef, useState } from 'react'
import { TOOL_META, type ToolId } from './editor/toolMeta'
import { useGitStatus } from './inspector/useGitStatus'
import { useRunningSubagents, type RunningSubagent } from '../hooks/useRunningSubagents'
import { useSpecProgress, type SpecCheckItem } from '../hooks/useSpecProgress'
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

// 折叠焦点窗：>6 条时以第一个未完成项为中心开 3 条（无未完成靠尾），
// 两端折成「前面/后面 N 项」（对齐 ZCode 的窗口规则在两态勾选上的退化形式）
function focusWindow(items: SpecCheckItem[]): { preceding: number; focus: SpecCheckItem[]; following: number } {
  if (items.length <= 6) return { preceding: 0, focus: items, following: 0 }
  let center = items.findIndex((it) => !it.done)
  if (center < 0) center = items.length - 1
  const start = Math.min(Math.max(0, center - 1), items.length - 3)
  const end = start + 3
  return { preceding: start, focus: items.slice(start, end), following: items.length - end }
}

// 已完成项的状态圈：绿色描边圆 + 内部小勾
function DoneCircle() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="var(--success)" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0, marginTop: '2px' }}>
      <circle cx="12" cy="12" r="9" />
      <path d="M8.5 12.5l2.5 2.5 4.5-5" />
    </svg>
  )
}

// 未完成项的状态圈：淡色空心圆
function EmptyCircle() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="var(--text-tertiary)" strokeWidth="1.8" style={{ flexShrink: 0, marginTop: '2px' }}>
      <circle cx="12" cy="12" r="9" />
    </svg>
  )
}

// 清单行：已完成灰字划线，未完成正常色；两行截断
function CheckRow({ item }: { item: SpecCheckItem }) {
  return (
    <div style={{ display: 'flex', alignItems: 'flex-start', gap: '8px', padding: '3px 4px' }}>
      {item.done ? <DoneCircle /> : <EmptyCircle />}
      <span
        style={{
          fontSize: '11px',
          fontFamily: 'var(--font-ui)',
          lineHeight: '17px',
          color: item.done ? 'var(--text-tertiary)' : 'var(--text-secondary)',
          textDecoration: item.done ? 'line-through' : 'none',
          display: '-webkit-box',
          WebkitLineClamp: 2,
          WebkitBoxOrient: 'vertical',
          overflow: 'hidden',
          wordBreak: 'break-all',
          minWidth: 0,
        }}
      >
        {item.text}
      </span>
    </div>
  )
}

// 区块统一样式：整行可点的分组行（hover 反馈由调用处行内样式处理）
function sectionStyle(): React.CSSProperties {
  return {
    display: 'flex',
    alignItems: 'center',
    gap: '8px',
    padding: '5px 8px',
    margin: '0 -4px',
    borderRadius: 'var(--radius-sm)',
    background: 'transparent',
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

// 状态点：复用标题栏呼吸灯语义（运行中蓝脉冲，就绪绿常亮）。
// checksPassed（验收全勾）时强制绿常亮——绿色此时表达「验收通过」而非「就绪」
function RunDot({ active, forceSuccess }: { active: boolean; forceSuccess?: boolean }) {
  const green = forceSuccess || !active
  return (
    <span
      style={{
        width: '7px',
        height: '7px',
        borderRadius: '50%',
        flexShrink: 0,
        backgroundColor: green ? 'var(--success)' : 'var(--info)',
        boxShadow: green ? 'var(--success-glow)' : 'var(--info-glow)',
        animation: active && !forceSuccess ? 'breathe 1.4s ease-in-out infinite' : 'none',
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

// 状态胶囊卡：收起态为活动摘要小胶囊（对齐 ZCode），
// 点击展开为固定宽度完整卡：常驻「进展」「产物」区块、「智能体」未常驻区块、
// 卡头 ··· 菜单与 ⤢ 收起钮。仍只在编辑区折叠时由 App 渲染（fixed 右上）。
function CapsuleCard({ onOpenTool, isTaskRunning, sessionId }: CapsuleCardProps) {
  const git = useGitStatus()
  const { running: runningAgents } = useRunningSubagents(sessionId)
  const { data: specData } = useSpecProgress()
  const blockCount = useChatStore((s) => s.blockIds.length)
  // 卡片形态：收起（小胶囊摘要，默认）/ 展开（固定宽度完整卡）
  const [expanded, setExpanded] = useState(false)
  const [menuOpen, setMenuOpen] = useState(false)
  const [agentsOpen, setAgentsOpen] = useState(false)
  const [specOpen, setSpecOpen] = useState(false)
  // 折叠行的「展开全部」状态
  const [specExpanded, setSpecExpanded] = useState(false)
  // 进展清单分组：任务（tasks.md）| 验证（checklist.md）
  const [specGroup, setSpecGroupState] = useState<'tasks' | 'checks'>('tasks')
  // 切分组时重置折叠窗
  const setSpecGroup = (group: 'tasks' | 'checks') => {
    setSpecGroupState(group)
    setSpecExpanded(false)
  }
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
  const spec = specData?.spec ?? null
  // 当前分组的清单与完成度（点击折叠行后展开全部）
  const activeGroup = specGroup === 'tasks' ? specData?.tasks : specData?.checks
  const groupItems = activeGroup?.items ?? []
  const { preceding, focus, following } = specExpanded
    ? { preceding: 0, focus: groupItems, following: 0 }
    : focusWindow(groupItems)
  // 验收清单全勾（且有条目）= 验收通过，进展区绿色强调
  const checks = specData?.checks
  const checksPassed = !!checks && checks.total > 0 && checks.done === checks.total

  // ---- 收起态：活动摘要小胶囊（对齐 ZCode 的「→ 当前活动」形态），点击展开 ----
  if (!expanded) {
    const specxy = checks && checks.total > 0 ? ` · ${checks.done}/${checks.total}` : ''
    return (
      <div
        onClick={() => setExpanded(true)}
        title="展开进展面板"
        style={{
          position: 'fixed',
          top: '44px',
          right: '14px',
          display: 'flex',
          alignItems: 'center',
          gap: '8px',
          padding: '8px 14px',
          maxWidth: '280px',
          borderRadius: '999px',
          backgroundColor: 'var(--bg-elevated)',
          boxShadow: 'var(--shadow-md)',
          border: '1px solid var(--border-subtle)',
          zIndex: 40,
          cursor: 'pointer',
          userSelect: 'none',
          transition: 'all var(--transition-fast)',
        }}
      >
        <RunDot active={isTaskRunning} forceSuccess={checksPassed} />
        <span
          style={{
            fontSize: '12px',
            fontFamily: 'var(--font-ui)',
            color: checksPassed ? 'var(--success)' : 'var(--text-secondary)',
            whiteSpace: 'nowrap',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
          }}
        >
          → {checksPassed ? '验收通过' : isTaskRunning ? '运行中' : '就绪'}
          {!checksPassed && spec ? specxy : ''}
        </span>
      </div>
    )
  }

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
        // 展开态固定宽度（对齐 ZCode 卡片的视觉比例），不随内容伸缩；spec 条目长文本由行内截断兜底
        width: '320px',
        boxSizing: 'border-box',
        borderRadius: 'var(--radius-lg)',
        backgroundColor: 'var(--bg-elevated)',
        boxShadow: 'var(--shadow-md)',
        border: '1px solid var(--border-subtle)',
        zIndex: 40,
        userSelect: 'none',
      }}
    >
      {/* 卡头：标题 + ⤢ 收起钮 + ··· 工具标签菜单 */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '8px', marginBottom: '2px' }}>
        <span style={sectionLabelStyle(true)}>进展</span>
        <div style={{ display: 'flex', alignItems: 'center', gap: '2px' }}>
          <button
            onClick={() => setExpanded(false)}
            title="收起进展面板"
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              width: '20px',
              height: '20px',
              border: 'none',
              borderRadius: 'var(--radius-sm)',
              background: 'transparent',
              color: 'var(--text-tertiary)',
              cursor: 'pointer',
              transition: 'all var(--transition-fast)',
              padding: 0,
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.background = 'var(--bg-base)'
              e.currentTarget.style.color = 'var(--text-primary)'
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.background = 'transparent'
              e.currentTarget.style.color = 'var(--text-tertiary)'
            }}
          >
            {/* 斜向收拢箭头（⤢ 反向） */}
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M9 3H3v6" />
              <path d="M15 21h6v-6" />
              <path d="M3 3l7 7" />
              <path d="M21 21l-7-7" />
            </svg>
          </button>
          <div ref={menuRef} style={{ position: 'relative' }}>          <button
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
      </div>

      {/* 进展（常驻）：有 spec 显示清单完成度并可展开；无 spec 维持运行态+工作块数并跳概要 */}
      {spec ? (
        <div
          style={sectionStyle()}
          onClick={() => setSpecOpen((v) => !v)}
          title="查看 spec 进展"
        >
          <RunDot active={isTaskRunning} forceSuccess={checksPassed} />
          <span style={sectionLabelStyle(false)}>{isTaskRunning ? '运行中' : '就绪'}</span>
          <span
            style={{
              ...sectionLabelStyle(checksPassed ? false : true),
              marginLeft: 'auto',
              color: checksPassed ? 'var(--success)' : undefined,
            }}
          >
            {activeGroup?.done ?? 0}/{activeGroup?.total ?? 0}
          </span>
        </div>
      ) : (
        <div style={sectionStyle()} onClick={() => onOpenTool('summary')} title="查看概要">
          <RunDot active={isTaskRunning} />
          <span style={sectionLabelStyle(false)}>{isTaskRunning ? '运行中' : '就绪'}</span>
          <span style={{ ...sectionLabelStyle(true), marginLeft: 'auto' }}>{blockCount} 个工作块</span>
        </div>
      )}

      {/* spec 清单展开区：任务|验证分组切换 + 三态行渲染 */}
      {spec && specOpen && (
        <div>
          {/* 分组切换：样式对齐侧栏 tab 分段控件 */}
          <div
            style={{
              display: 'flex',
              gap: '2px',
              padding: '2px',
              margin: '2px 0',
              borderRadius: 'var(--radius-md)',
              background: 'var(--bg-base)',
            }}
          >
            {(
              [
                { key: 'tasks', label: '任务' },
                { key: 'checks', label: '验证' },
              ] as const
            ).map(({ key, label }) => {
              const selected = specGroup === key
              return (
                <button
                  key={key}
                  onClick={() => setSpecGroup(key)}
                  style={{
                    flex: 1,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    gap: '4px',
                    padding: '3px 0',
                    border: 'none',
                    borderRadius: 'var(--radius-sm)',
                    background: selected ? 'var(--selected-bg)' : 'transparent',
                    color: selected ? 'var(--text-primary)' : 'var(--text-tertiary)',
                    fontSize: '11px',
                    fontFamily: 'var(--font-ui)',
                    fontWeight: selected ? 600 : 500,
                    cursor: 'pointer',
                    transition: 'all var(--transition-fast)',
                  }}
                >
                  {label}
                </button>
              )
            })}
          </div>
          {/* 折叠行：前面 N 项（点击展开全部） */}
          {preceding > 0 && (
            <div
              style={{ ...sectionStyle(), justifyContent: 'center', cursor: 'pointer' }}
              onClick={(e) => {
                e.stopPropagation()
                setSpecExpanded(true)
              }}
              title="展开全部"
            >
              <span style={sectionLabelStyle(true)}>前面 {preceding} 项</span>
            </div>
          )}
          {focus.map((it, i) => (
            <CheckRow key={`${spec.name}-${i}-${it.text}`} item={it} />
          ))}
          {/* 折叠行：后面 N 项 */}
          {following > 0 && (
            <div
              style={{ ...sectionStyle(), justifyContent: 'center', cursor: 'pointer' }}
              onClick={(e) => {
                e.stopPropagation()
                setSpecExpanded(true)
              }}
              title="展开全部"
            >
              <span style={sectionLabelStyle(true)}>后面 {following} 项</span>
            </div>
          )}
          {/* 空清单提示 */}
          {groupItems.length === 0 && (
            <div style={{ padding: '4px', fontSize: '11px', color: 'var(--text-tertiary)', fontFamily: 'var(--font-ui)' }}>
              该分组暂无条目
            </div>
          )}
        </div>
      )}

      {/* 产物（常驻）：git 变更数，点击跳审查 */}
      <div style={sectionStyle()} onClick={() => onOpenTool('review')} title="查看审查详情">
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
          <div style={sectionStyle()} onClick={() => setAgentsOpen((v) => !v)} title="运行中的智能体">
            <RunDot active />
            <span style={sectionLabelStyle(false)}>智能体</span>
            <span style={{ ...sectionLabelStyle(true), marginLeft: 'auto' }}>{runningAgents.length} 运行中</span>
          </div>
          {agentsOpen &&
            runningAgents.map((a) => (
              <div
                key={a.agent_id}
                style={{ ...sectionStyle(), paddingLeft: '20px' }}
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
