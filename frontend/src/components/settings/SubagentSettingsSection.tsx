// 子智能体区 - 展示内置 + 自定义代理，用户级可增删改
// 接 GET/POST/DELETE /api/agents；项目级与内置只读

import { useEffect, useState, useCallback } from 'react'
import { agentsApi } from '../../api/client'
import type { AgentInfo, AgentDiagnostic, AgentCreateInput } from '../../api/client'

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

const SOURCE_LABEL: Record<string, string> = {
  'built-in': '内置',
  user: '用户级',
  project: '项目级',
}

// 空 表单（新建用）
const EMPTY_FORM: AgentCreateInput & { system_prompt: string } = {
  name: '',
  description: '',
  system_prompt: '',
  tools: null,
  model: '',
  max_turns: null,
  background: false,
}

function AgentForm({
  initial,
  onSave,
  onCancel,
}: {
  initial: AgentCreateInput & { system_prompt: string }
  onSave: (data: AgentCreateInput & { system_prompt: string }) => Promise<void>
  onCancel: () => void
}) {
  const [form, setForm] = useState(initial)
  const [toolsText, setToolsText] = useState(initial.tools ? initial.tools.join(', ') : '')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const submit = async () => {
    if (!form.name.trim() || !form.description.trim()) {
      setError('name 与 description 必填')
      return
    }
    setSaving(true)
    setError('')
    try {
      await onSave({
        ...form,
        name: form.name.trim(),
        description: form.description.trim(),
        tools: toolsText.trim() ? toolsText.split(',').map((t) => t.trim()).filter(Boolean) : null,
        model: form.model?.trim() || null,
      })
    } catch (e) {
      setError(e instanceof Error ? e.message : '保存失败')
    } finally {
      setSaving(false)
    }
  }

  const inputStyle: React.CSSProperties = {
    width: '100%',
    padding: '6px 8px',
    background: 'var(--bg-primary)',
    border: '1px solid var(--border)',
    borderRadius: 'var(--radius-sm)',
    color: 'var(--text-primary)',
    fontSize: '12px',
    fontFamily: 'var(--font-mono)',
    boxSizing: 'border-box',
  }
  const labelStyle: React.CSSProperties = {
    fontSize: '11px',
    color: 'var(--text-tertiary)',
    marginBottom: '4px',
    display: 'block',
  }

  return (
    <div
      className="agent-form"
      style={{
        padding: '14px',
        backgroundColor: 'var(--bg-primary)',
        border: '1px solid var(--border)',
        borderRadius: 'var(--radius-md)',
        display: 'flex',
        flexDirection: 'column',
        gap: '10px',
      }}
    >
      <div style={{ display: 'grid', 'gridTemplateColumns': '1fr 1fr', gap: '10px' } as React.CSSProperties}>
        <div>
          <label style={labelStyle}>名称（agent_type，必填）</label>
          <input
            className="agent-form-name"
            style={inputStyle}
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
            placeholder="reviewer"
          />
        </div>
        <div>
          <label style={labelStyle}>模型（空 = 继承主循环）</label>
          <input
            style={inputStyle}
            value={form.model || ''}
            onChange={(e) => setForm({ ...form, model: e.target.value })}
            placeholder="glm-4.7"
          />
        </div>
      </div>
      <div>
        <label style={labelStyle}>用途说明（必填，何时使用该代理）</label>
        <input
          className="agent-form-desc"
          style={inputStyle}
          value={form.description}
          onChange={(e) => setForm({ ...form, description: e.target.value })}
          placeholder="审查代码变更，输出改进建议"
        />
      </div>
      <div style={{ display: 'grid', 'gridTemplateColumns': '1fr 120px', gap: '10px' } as React.CSSProperties}>
        <div>
          <label style={labelStyle}>工具白名单（逗号分隔，空 = 全部工具）</label>
          <input
            style={inputStyle}
            value={toolsText}
            onChange={(e) => setToolsText(e.target.value)}
            placeholder="Read, Grep, Glob"
          />
        </div>
        <div>
          <label style={labelStyle}>最大轮数</label>
          <input
            style={inputStyle}
            value={form.max_turns ?? ''}
            onChange={(e) => {
              const v = e.target.value.trim()
              setForm({ ...form, max_turns: v && /^\d+$/.test(v) ? Number(v) : null })
            }}
            placeholder="不限"
          />
        </div>
      </div>
      <label style={{ fontSize: '12px', color: 'var(--text-secondary)', display: 'flex', alignItems: 'center', gap: '6px' }}>
        <input
          type="checkbox"
          checked={form.background}
          onChange={(e) => setForm({ ...form, background: e.target.checked })}
        />
        总是后台运行
      </label>
      <div>
        <label style={labelStyle}>系统提示词（markdown 正文）</label>
        <textarea
          className="agent-form-prompt"
          style={{ ...inputStyle, minHeight: '80px', resize: 'vertical' }}
          value={form.system_prompt}
          onChange={(e) => setForm({ ...form, system_prompt: e.target.value })}
          placeholder="你是……"
        />
      </div>
      {error && <div style={{ color: 'var(--error)', fontSize: '12px' }}>{error}</div>}
      <div style={{ display: 'flex', gap: '8px' }}>
        <button
          className="agent-form-save"
          type="button"
          disabled={saving}
          onClick={submit}
          style={{
            padding: '5px 14px',
            fontSize: '12px',
            background: 'var(--accent, #4da3ff)',
            border: 'none',
            borderRadius: 'var(--radius-sm)',
            color: '#fff',
            cursor: saving ? 'default' : 'pointer',
          }}
        >
          {saving ? '保存中...' : '保存'}
        </button>
        <button
          type="button"
          onClick={onCancel}
          style={{
            padding: '5px 14px',
            fontSize: '12px',
            background: 'transparent',
            border: '1px solid var(--border)',
            borderRadius: 'var(--radius-sm)',
            color: 'var(--text-secondary)',
            cursor: 'pointer',
          }}
        >
          取消
        </button>
      </div>
    </div>
  )
}

function SubagentSettingsSection() {
  const [agents, setAgents] = useState<AgentInfo[]>([])
  const [diagnostics, setDiagnostics] = useState<AgentDiagnostic[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [editing, setEditing] = useState<AgentCreateInput & { system_prompt: string } | null>(null)

  const load = useCallback(async () => {
    try {
      const data = await agentsApi.list()
      setAgents(data.agents)
      setDiagnostics(data.diagnostics || [])
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载子智能体失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const save = async (data: AgentCreateInput & { system_prompt: string }) => {
    await agentsApi.create(data)
    setEditing(null)
    await load()
  }

  const remove = async (name: string) => {
    try {
      await agentsApi.remove(name)
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : '删除失败')
    }
  }

  if (loading) {
    return <div style={{ color: 'var(--text-secondary)', fontSize: '13px' }}>加载中...</div>
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
        子智能体定义（内置 + 自定义）。自定义代理从 .md 文件加载：用户级 ~/.agent/agents/ 可在下方增删改，
        项目级 .agent/agents/ 只读（其权限配置不生效，防提权）。
      </div>

      {error && <div style={{ color: 'var(--error)', fontSize: '12px', marginBottom: '12px' }}>{error}</div>}

      {diagnostics.length > 0 && (
        <div
          className="agent-diagnostics"
          style={{
            marginBottom: '12px',
            padding: '10px 12px',
            border: '1px solid var(--warning, #d29922)',
            borderRadius: 'var(--radius-md)',
            fontSize: '12px',
            color: 'var(--text-secondary)',
          }}
        >
          <div style={{ marginBottom: '6px', color: 'var(--warning, #d29922)' }}>部分自定义代理加载失败：</div>
          {diagnostics.map((d, i) => (
            <div key={i} style={{ fontFamily: 'var(--font-mono)', fontSize: '11px' }}>
              {d.file}：{d.message}
            </div>
          ))}
        </div>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
        {editing ? (
          <AgentForm initial={editing} onSave={save} onCancel={() => setEditing(null)} />
        ) : (
          <button
            className="agent-create"
            type="button"
            onClick={() => setEditing({ ...EMPTY_FORM })}
            style={{
              alignSelf: 'flex-start',
              padding: '6px 16px',
              fontSize: '12px',
              background: 'transparent',
              border: '1px solid var(--border)',
              borderRadius: 'var(--radius-sm)',
              color: 'var(--text-secondary)',
              cursor: 'pointer',
            }}
          >
            + 新建自定义代理
          </button>
        )}

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
                {SOURCE_LABEL[a.source] ?? a.source}
              </span>
              {/* 用户级可编辑/删除 */}
              {a.source === 'user' && !editing && (
                <span style={{ marginLeft: 'auto', display: 'flex', gap: '6px' }}>
                  <button
                    type="button"
                    onClick={() =>
                      setEditing({
                        name: a.agent_type,
                        description: a.when_to_use,
                        system_prompt: '',
                        tools: a.tools,
                        model: a.model,
                        max_turns: a.max_turns,
                        background: a.background,
                      })
                    }
                    style={{
                      padding: '2px 10px',
                      fontSize: '11px',
                      background: 'transparent',
                      border: '1px solid var(--border)',
                      borderRadius: 'var(--radius-sm)',
                      color: 'var(--text-secondary)',
                      cursor: 'pointer',
                    }}
                  >
                    编辑
                  </button>
                  <button
                    type="button"
                    onClick={() => remove(a.agent_type)}
                    style={{
                      padding: '2px 10px',
                      fontSize: '11px',
                      background: 'transparent',
                      border: '1px solid var(--border)',
                      borderRadius: 'var(--radius-sm)',
                      color: 'var(--error)',
                      cursor: 'pointer',
                    }}
                  >
                    删除
                  </button>
                </span>
              )}
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
