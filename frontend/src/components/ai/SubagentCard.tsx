import { useEffect, useMemo, useState } from 'react'
import type { TimelineItem } from '../../stores/useChatStore'

// 子代理状态卡片：Agent 工具调用的独立展示位。
// - 运行中：轮询详情 + 输出，实时显示最近工具活动、已完成工具调用数与中间输出（防"像卡死"）
// - 完成后：展示 usage 与最终结果，可展开完整输出
// 数据来自 /api/subagents 系列端点；agent_id 优先从结果文本解析，
// 结果未回来时按 description 在本会话运行中子代理里匹配。

interface SubagentInfo {
  agent_id: string
  agent_type: string
  description: string
  status: string
  mode: string
  created_at: number
  updated_at: number
  usage?: { total_tokens?: number; tool_uses?: number; duration_ms?: number }
  error?: string | null
}

interface SubagentOutput {
  status: string
  output: string
  last_tool?: string | null
  tool_calls_done?: number
}

const STATUS_LABEL: Record<string, string> = {
  pending: '等待中',
  running: '运行中',
  completed: '已完成',
  failed: '失败',
  aborted: '已中断',
  stopped: '已停止',
}

const STATUS_COLOR: Record<string, string> = {
  pending: 'var(--text-tertiary)',
  running: 'var(--accent)',
  completed: 'var(--success)',
  failed: 'var(--error)',
  aborted: 'var(--warning)',
  stopped: 'var(--text-tertiary)',
}

// 从结果文本解析 agent_id（结果头行 [subagent_id: agent_xxx ...] / 尾部 / 后台启动文案）
function parseAgentId(result?: string): string | null {
  if (!result) return null
  const m = result.match(/agent_[0-9a-f]{6,}/)
  return m ? m[0] : null
}

function parseArgs(args: string): { description?: string; subagent_type?: string; run_in_background?: boolean } {
  if (!args) return {}
  try {
    return JSON.parse(args)
  } catch {
    return {}
  }
}

function formatDurationMs(ms?: number): string {
  if (!ms || ms < 0) return '-'
  if (ms < 1000) return `${ms}ms`
  const s = Math.floor(ms / 1000)
  if (s < 60) return `${s}s`
  return `${Math.floor(s / 60)}m${s % 60}s`
}

export default function SubagentCard({ step }: { step: TimelineItem }) {
  // TimelineItem 的 args 为可选字段（仅 tool 项有值），解析前非空兜底
  const { description, subagent_type: agentType } = useMemo(() => parseArgs(step.args ?? ''), [step.args])
  const agentIdFromResult = useMemo(() => parseAgentId(step.result), [step.result])
  const [agentId, setAgentId] = useState<string | null>(agentIdFromResult)
  const [info, setInfo] = useState<SubagentInfo | null>(null)
  const [output, setOutput] = useState<SubagentOutput | null>(null)
  const [stopping, setStopping] = useState(false)
  const [showFull, setShowFull] = useState(false)
  const [now, setNow] = useState(Date.now())

  // 结果文本到位后更新 agent_id
  useEffect(() => {
    if (agentIdFromResult) setAgentId(agentIdFromResult)
  }, [agentIdFromResult])

  const isRunning = !!info && (info.status === 'running' || info.status === 'pending')
  const isTerminal = !!info && info.status !== 'running' && info.status !== 'pending'

  // 轮询详情 + 输出：运行中每 2s 刷新（实时反馈）；结束后补取一次最终输出
  useEffect(() => {
    let cancelled = false
    async function poll() {
      if (cancelled) return
      let target = agentId
      // 结果未回来（前台在跑/后台刚启动）：按 description 匹配本会话运行中的子代理
      if (!target && description) {
        try {
          const resp = await fetch('/api/subagents')
          const data = await resp.json()
          const match = (data.subagents as SubagentInfo[]).find(
            s => s.description === description && (s.status === 'running' || s.status === 'pending'),
          )
          if (match) {
            target = match.agent_id
            if (!cancelled) setAgentId(match.agent_id)
          }
        } catch { /* 网络异常下轮自然重试 */ }
      }
      if (!target || cancelled) return
      try {
        const [detailResp, outResp] = await Promise.all([
          fetch(`/api/subagents/${target}`),
          fetch(`/api/subagents/${target}/output`),
        ])
        if (detailResp.ok) {
          const detail = await detailResp.json()
          if (!cancelled) setInfo(detail)
        }
        if (outResp.ok) {
          const out = await outResp.json()
          if (!cancelled) setOutput(out)
        }
      } catch { /* 忽略单次失败 */ }
      if (!cancelled) setNow(Date.now())
    }
    poll()
    let timer: number | undefined
    if (!isTerminal) {
      timer = window.setInterval(poll, 2000)
    }
    return () => {
      cancelled = true
      if (timer) window.clearInterval(timer)
    }
  }, [agentId, description, isTerminal])

  const status = info?.status ?? (step.isRunning ? 'running' : 'unknown')
  const statusLabel = STATUS_LABEL[status] ?? '未知'
  const usage = info?.usage
  // 运行中实时耗时：从创建时间推算（usage 结束才写，运行期用不上）
  const elapsedMs = isRunning && info
    ? Math.max(0, now - info.created_at * 1000)
    : usage?.duration_ms

  // 实时活动文案：最近工具 + 已完成调用数
  const liveActivity = (() => {
    if (!isRunning) return ''
    if (output?.last_tool) return `正在执行工具 ${output.last_tool} · 已完成 ${output.tool_calls_done ?? 0} 次工具调用`
    if (output?.tool_calls_done) return `已完成 ${output.tool_calls_done} 次工具调用，正在生成回复`
    return '正在启动，等待子代理产出...'
  })()

  // 中间输出展示：运行中实时片段；完成后前 200 字预览 + 展开完整
  const displayOutput = (() => {
    if (!output) return ''
    if (isRunning) return output.output
    return output.output
  })()

  async function handleStop() {
    if (!agentId) return
    setStopping(true)
    try {
      await fetch(`/api/subagents/${agentId}/stop`, { method: 'POST' })
    } catch { /* 下轮轮询会反映状态 */ }
    setStopping(false)
  }

  const outputPreview = !isRunning && !showFull ? displayOutput.slice(0, 200) : displayOutput

  return (
    <div
      className="subagent-card"
      data-agent-id={agentId ?? undefined}
      data-status={status}
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: '4px',
        padding: '8px 10px',
        border: '1px solid var(--border-subtle)',
        borderRadius: 'var(--radius-md)',
        background: 'var(--bg-base, transparent)',
        fontSize: '11px',
        fontFamily: 'var(--font-ui)',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
        <StepBadge />
        <span style={{ fontWeight: 500, color: 'var(--text-secondary)' }}>
          子任务 · {agentType || 'general-purpose'}
        </span>
        <span
          className="subagent-status"
          style={{ marginLeft: 'auto', color: STATUS_COLOR[status] ?? 'var(--text-tertiary)', flexShrink: 0 }}
        >
          {isRunning && (
            <span
              style={{
                display: 'inline-block',
                width: '6px',
                height: '6px',
                borderRadius: '50%',
                backgroundColor: STATUS_COLOR[status],
                marginRight: '4px',
                animation: 'breathe 1.6s ease-in-out infinite',
              }}
            />
          )}
          {statusLabel}
        </span>
      </div>

      {description && (
        <div style={{ color: 'var(--text-secondary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {description}
        </div>
      )}

      {/* 运行中：实时活动行（关键防"像卡死"反馈） */}
      {isRunning && (
        <div className="subagent-live" style={{ color: 'var(--text-tertiary)', fontSize: '10px' }}>
          {liveActivity}
        </div>
      )}

      <div
        className="subagent-usage"
        style={{ display: 'flex', gap: '12px', color: 'var(--text-tertiary)', fontFamily: 'var(--font-mono)', fontSize: '10px' }}
      >
        <span>耗时 {formatDurationMs(elapsedMs)}</span>
        <span>tokens {usage?.total_tokens ?? '-'}</span>
        <span>工具 {usage?.tool_uses ?? output?.tool_calls_done ?? '-'}</span>
        {info?.mode === 'background' && <span>后台</span>}
      </div>

      {info?.error && (
        <div style={{ color: 'var(--error)', fontSize: '10px' }}>原因：{info.error}</div>
      )}

      {/* 中间/最终输出：运行中实时滚动；完成后预览 + 展开完整 */}
      {displayOutput && (
        <div className="subagent-output-wrap">
          <div
            className={isRunning ? 'subagent-live-output' : 'subagent-output'}
            style={{
              color: 'var(--text-tertiary)',
              fontFamily: 'var(--font-mono)',
              fontSize: '10px',
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
              maxHeight: isRunning ? '120px' : showFull ? '320px' : '60px',
              overflow: 'auto',
              border: '1px solid var(--border-subtle)',
              borderRadius: 'var(--radius-sm)',
              padding: '4px 6px',
            }}
          >
            {outputPreview}
          </div>
          {!isRunning && output && output.output.length > 200 && (
            <button
              type="button"
              onClick={() => setShowFull(v => !v)}
              style={{
                alignSelf: 'flex-start',
                marginTop: '4px',
                padding: '1px 8px',
                fontSize: '10px',
                background: 'transparent',
                border: '1px solid var(--border-subtle)',
                borderRadius: 'var(--radius-sm)',
                color: 'var(--text-tertiary)',
                cursor: 'pointer',
              }}
            >
              {showFull ? '收起完整结果' : '展开完整结果'}
            </button>
          )}
        </div>
      )}

      {!isTerminal && agentId && (
        <button
          type="button"
          className="subagent-stop"
          onClick={handleStop}
          disabled={stopping}
          style={{
            alignSelf: 'flex-start',
            padding: '2px 8px',
            fontSize: '10px',
            background: 'transparent',
            border: '1px solid var(--border-subtle)',
            borderRadius: 'var(--radius-sm)',
            color: 'var(--text-tertiary)',
            cursor: stopping ? 'default' : 'pointer',
          }}
        >
          {stopping ? '停止中...' : '停止'}
        </button>
      )}
    </div>
  )
}

function StepBadge() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="var(--text-tertiary)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0 }}>
      <rect x="4" y="8" width="16" height="12" rx="2" />
      <path d="M12 8V4" />
      <path d="M8 13h.01M16 13h.01" />
    </svg>
  )
}
