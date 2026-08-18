// 多智能体分区 — 展示团队、成员与当前活跃 teammate（接 GET /api/team/teammates）
import { useEffect, useState } from 'react'
import { apiGet } from '../../api/client'

interface TeamMember {
  name: string
  agent_id?: string
}

interface TeamInfo {
  name: string
  members: TeamMember[]
}

interface ActiveTeammate {
  name: string
  status: string | null
  team_name: string | null
}

interface TeamOverview {
  teams: TeamInfo[]
  active_teammates: ActiveTeammate[]
}

const STATUS_LABEL: Record<string, string> = {
  running: '运行中',
  idle: '空闲',
  stopped: '已停止',
}

function MultiagentSection() {
  const [data, setData] = useState<TeamOverview | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    apiGet<TeamOverview>('/api/team/teammates')
      .then(setData)
      .catch((e) => setError(e instanceof Error ? e.message : '加载团队信息失败'))
      .finally(() => setLoading(false))
  }, [])

  if (loading) {
    return <div style={{ color: 'var(--text-secondary)', fontSize: '13px' }}>加载中...</div>
  }
  if (error) {
    return <div style={{ color: 'var(--error)', fontSize: '13px' }}>{error}</div>
  }

  const teams = data?.teams ?? []
  const active = data?.active_teammates ?? []

  if (teams.length === 0) {
    return (
      <div style={{ padding: '40px 20px', textAlign: 'center', color: 'var(--text-tertiary)' }}>
        <div style={{ fontSize: '32px', marginBottom: '12px' }}>🤖</div>
        <div style={{ fontSize: '13px' }}>暂无团队。使用 TeamCreate 工具创建团队后在此展示。</div>
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
      <div style={{ fontSize: '13px', color: 'var(--text-secondary)', lineHeight: 1.6 }}>
        多智能体协作（对等 teammate）。团队与任务图由 leader 经 TeamCreate / TaskCreate 工具管理，
        此处只读展示成员与实时状态。
      </div>

      {teams.map((team) => (
        <div
          key={team.name}
          style={{
            padding: '14px',
            backgroundColor: 'var(--bg-primary)',
            border: '1px solid var(--border)',
            borderRadius: 'var(--radius-md)',
          }}
        >
          <div style={{ fontSize: '14px', fontWeight: 600, color: 'var(--text-primary)', marginBottom: '8px' }}>
            {team.name}
          </div>
          {team.members.length === 0 ? (
            <div style={{ fontSize: '12px', color: 'var(--text-tertiary)' }}>暂无成员</div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
              {team.members.map((m) => {
                const runtime = active.find((a) => a.team_name === team.name && a.name === m.name)
                return (
                  <div key={m.name} style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '12px' }}>
                    <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-primary)' }}>{m.name}</span>
                    {m.agent_id && (
                      <span style={{ color: 'var(--text-tertiary)', fontFamily: 'var(--font-mono)', fontSize: '11px' }}>
                        {m.agent_id}
                      </span>
                    )}
                    <span
                      style={{
                        marginLeft: 'auto',
                        fontSize: '11px',
                        padding: '1px 8px',
                        borderRadius: 'var(--radius-sm)',
                        border: '1px solid var(--border)',
                        color: runtime ? 'var(--text-secondary)' : 'var(--text-tertiary)',
                      }}
                    >
                      {runtime ? (STATUS_LABEL[runtime.status ?? ''] ?? runtime.status) : '未运行'}
                    </span>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      ))}
    </div>
  )
}

export default MultiagentSection
