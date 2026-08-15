// 子智能体区 — 只读展示内置代理（general-purpose / Explore）
// 接 GET /api/agents

import { useEffect, useState } from 'react'
import { agentsApi } from '../../api/client'
import type { AgentInfo } from '../../api/client'

// 工具列表的展示：通配符显示"全部工具"
function formatTools(tools: string[] | null, disallowed: string[]): string {
  if (tools === null || (tools && tools.length === 1 && tools[0] === '*')) {
    if (disallowed.length > 0) {
      return `全部工具（排除 ${disallowed.join(', ')}）`
    }
    return '全部工具'
  }
  return tools?.join(', ') || '无'
}

function SubagentSettingsSection() {
  const [agents, setAgents] = useState<AgentInfo[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    const load = async () => {
      try {
        const data = await agentsApi.list()
        setAgents(data.agents)
      } catch (e) {
        setError(e instanceof Error ? e.message : '加载子智能体失败')
      } finally {
        setLoading(false)
      }
    }
    load()
  }, [])

  if (loading) {
    return <div style={{ color: 'var(--text-secondary)', fontSize: '13px' }}>加载中...</div>
  }

  if (error) {
    return <div style={{ color: 'var(--error)', fontSize: '13px' }}>{error}</div>
  }

  if (agents.length === 0) {
    return (
      <div style={{ padding: '40px 20px', textAlign: 'center', color: 'var(--text-tertiary)' }}>
        <div style={{ fontSize: '32px', marginBottom: '12px' }}>🤖</div>
        <div style={{ fontSize: '13px' }}>暂无子智能体</div>
      </div>
    )
  }

  return (
    <div>
      <div
        style={{
          fontSize: '13px',
          color: 'var(--text-secondary)',
          marginBottom: '16px',
          lineHeight: 1.6,
        }}
      >
        内置子智能体定义。主 LLM 通过 Agent 工具派生子代理时，根据 agent_type 查找对应定义。
        当前为只读展示，自定义代理能力待后续实现。
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
        {agents.map((a) => (
          <div
            key={a.agent_type}
            style={{
              padding: '14px',
              backgroundColor: 'var(--bg-primary)',
              border: '1px solid var(--border)',
              borderRadius: 'var(--radius-md)',
            }}
          >
            {/* 标题行 */}
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '8px' }}>
              <span style={{ fontSize: '14px', fontWeight: 600, color: 'var(--text-primary)' }}>
                {a.agent_type}
              </span>
              {a.background && (
                <span
                  style={{
                    fontSize: '10px',
                    padding: '1px 6px',
                    backgroundColor: 'rgba(255, 255, 255, 0.08)',
                    color: 'var(--text-secondary)',
                    borderRadius: 'var(--radius-sm)',
                  }}
                >
                  后台
                </span>
              )}
              <span
                style={{
                  fontSize: '10px',
                  padding: '1px 6px',
                  border: '1px solid var(--border)',
                  color: 'var(--text-tertiary)',
                  borderRadius: 'var(--radius-sm)',
                }}
              >
                {a.source}
              </span>
            </div>

            {/* 用途说明 */}
            <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '10px', lineHeight: 1.5 }}>
              {a.when_to_use}
            </div>

            {/* 字段网格 */}
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: 'auto 1fr',
                gap: '6px 12px',
                fontSize: '12px',
                fontFamily: 'var(--font-mono)',
              }}
            >
              <span style={{ color: 'var(--text-tertiary)' }}>模型</span>
              <span style={{ color: 'var(--text-primary)' }}>
                {a.model === null || a.model === 'inherit' ? '继承主循环' : a.model}
              </span>

              <span style={{ color: 'var(--text-tertiary)' }}>工具</span>
              <span style={{ color: 'var(--text-primary)' }}>
                {formatTools(a.tools, a.disallowed_tools)}
              </span>

              <span style={{ color: 'var(--text-tertiary)' }}>最大轮数</span>
              <span style={{ color: 'var(--text-primary)' }}>
                {a.max_turns === null ? '不限' : a.max_turns}
              </span>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

export default SubagentSettingsSection
