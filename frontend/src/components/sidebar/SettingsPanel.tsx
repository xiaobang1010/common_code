import { useEffect, useState } from 'react'

// LLM 配置结构
interface LLMConfig {
  llm_base_url: string
  llm_api_key: string
  llm_model: string
  llm_providers?: { name: string; base_url: string; model: string }[]
  active_provider?: string | null
}

// 插件结构
interface PluginInfo {
  name: string
  version: string
  kind: string
  enabled: boolean
  description: string
}

function SettingsPanel() {
  const [baseUrl, setBaseUrl] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [model, setModel] = useState('')
  const [showKey, setShowKey] = useState(false)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  // LLM 供应商
  const [providers, setProviders] = useState<{ name: string; base_url: string; model: string }[]>([])
  const [activeProvider, setActiveProvider] = useState<string | null>(null)

  // 插件列表
  const [plugins, setPlugins] = useState<PluginInfo[]>([])

  // 加载当前配置
  useEffect(() => {
    const loadConfig = async () => {
      try {
        const res = await fetch('/api/config')
        const json = await res.json()
        setBaseUrl(json.llm_base_url || '')
        setApiKey(json.llm_api_key || '')
        setModel(json.llm_model || '')
        setProviders(json.llm_providers || [])
        setActiveProvider(json.active_provider || null)
      } catch (e) {
        setError('加载配置失败')
        console.error(e)
      } finally {
        setLoading(false)
      }
    }
    loadConfig()

    // 加载插件列表
    fetch('/api/plugins')
      .then(r => r.json())
      .then(data => setPlugins(data.plugins || []))
      .catch(() => {})
  }, [])

  // 保存配置
  const handleSave = async () => {
    setSaving(true)
    setError('')
    setMessage('')
    const config: LLMConfig = {
      llm_base_url: baseUrl,
      llm_api_key: apiKey,
      llm_model: model,
    }
    try {
      const res = await fetch('/api/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(config),
      })
      const json = await res.json()
      if (!json.ok) {
        setError(json.error || '保存失败')
        return
      }
      setMessage('配置已保存，重启后生效')
      // 3 秒后提示消失
      setTimeout(() => setMessage(''), 3000)
    } catch (e) {
      setError('保存失败')
      console.error(e)
    } finally {
      setSaving(false)
    }
  }

  if (loading) {
    return (
      <div style={{ padding: '8px', color: 'var(--text-secondary)', fontSize: '13px' }}>
        加载中...
      </div>
    )
  }

  // 输入框通用样式（带类型注解，保证字面量类型被正确收窄）
  const inputStyle: React.CSSProperties = {
    width: '100%',
    boxSizing: 'border-box',
    backgroundColor: 'var(--bg-primary)',
    border: '1px solid var(--border)',
    color: 'var(--text-primary)',
    padding: '4px 8px',
    fontSize: '13px',
    outline: 'none',
    borderRadius: '2px',
  }

  const labelStyle: React.CSSProperties = {
    display: 'block',
    color: 'var(--text-secondary)',
    fontSize: '12px',
    marginBottom: '4px',
  }

  return (
    <div style={{ flex: 1, overflow: 'auto', padding: '12px' }}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
        {/* Base URL */}
        <div>
          <label style={labelStyle}>Base URL</label>
          <input
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
            placeholder="https://api.example.com/v1"
            style={inputStyle}
          />
        </div>
        {/* API Key（脱敏显示，眼睛图标切换） */}
        <div>
          <label style={labelStyle}>API Key</label>
          <div style={{ display: 'flex', gap: '4px' }}>
            <input
              type={showKey ? 'text' : 'password'}
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder="sk-..."
              style={{ ...inputStyle, flex: 1 }}
            />
            <button
              onClick={() => setShowKey(!showKey)}
              title={showKey ? '隐藏' : '显示'}
              style={{
                border: '1px solid var(--border)',
                backgroundColor: 'var(--bg-primary)',
                color: 'var(--text-secondary)',
                width: '30px',
                cursor: 'pointer',
                fontSize: '14px',
                borderRadius: '2px',
                flexShrink: 0,
              }}
            >
              {showKey ? '🙈' : '👁'}
            </button>
          </div>
        </div>
        {/* Model */}
        <div>
          <label style={labelStyle}>Model</label>
          <input
            value={model}
            onChange={(e) => setModel(e.target.value)}
            placeholder="gpt-4o"
            style={inputStyle}
          />
        </div>
        {/* LLM 供应商切换（有插件供应商时显示） */}
        {providers.length > 0 && (
          <div>
            <label style={labelStyle}>LLM 供应商</label>
            <select
              value={activeProvider || ''}
              onChange={async (e) => {
                const val = e.target.value
                try {
                  await fetch('/api/plugins/llm-provider/switch', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ provider: val }),
                  })
                  setActiveProvider(val)
                  setMessage('供应商已切换')
                  setTimeout(() => setMessage(''), 3000)
                } catch {
                  setError('切换失败')
                }
              }}
              style={inputStyle}
            >
              {providers.map(p => (
                <option key={p.name} value={p.name}>
                  {p.name} ({p.model})
                </option>
              ))}
            </select>
          </div>
        )}

        {/* 插件列表 */}
        {plugins.length > 0 && (
          <div>
            <label style={labelStyle}>插件</label>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
              {plugins.map(p => (
                <div
                  key={p.name}
                  style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                    padding: '6px 8px',
                    backgroundColor: 'var(--bg-primary)',
                    border: '1px solid var(--border)',
                    borderRadius: '2px',
                    fontSize: '12px',
                  }}
                >
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ color: 'var(--text-primary)', fontWeight: 500 }}>
                      {p.name}
                      <span style={{ color: 'var(--text-tertiary)', marginLeft: '6px', fontSize: '11px' }}>
                        v{p.version} · {p.kind}
                      </span>
                    </div>
                    {p.description && (
                      <div style={{ color: 'var(--text-tertiary)', fontSize: '11px', marginTop: '2px' }}>
                        {p.description}
                      </div>
                    )}
                  </div>
                  <button
                    onClick={async () => {
                      const endpoint = p.enabled ? '/api/plugins/disable' : '/api/plugins/enable'
                      try {
                        await fetch(endpoint, {
                          method: 'POST',
                          headers: { 'Content-Type': 'application/json' },
                          body: JSON.stringify({ name: p.name }),
                        })
                        // 刷新列表
                        const res = await fetch('/api/plugins')
                        const data = await res.json()
                        setPlugins(data.plugins || [])
                      } catch {
                        setError('操作失败')
                      }
                    }}
                    style={{
                      border: '1px solid var(--border)',
                      backgroundColor: p.enabled ? 'var(--accent-soft)' : 'var(--bg-primary)',
                      color: p.enabled ? 'var(--accent)' : 'var(--text-tertiary)',
                      padding: '2px 10px',
                      fontSize: '11px',
                      cursor: 'pointer',
                      borderRadius: '2px',
                      flexShrink: 0,
                    }}
                  >
                    {p.enabled ? '已启用' : '已禁用'}
                  </button>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* 保存按钮 */}
        <button
          onClick={handleSave}
          disabled={saving}
          style={{
            width: '100%',
            border: 'none',
            backgroundColor: saving ? 'var(--bg-primary)' : 'var(--accent)',
            color: saving ? 'var(--text-secondary)' : '#fff',
            padding: '6px 8px',
            fontSize: '13px',
            cursor: saving ? 'not-allowed' : 'pointer',
            borderRadius: '2px',
          }}
        >
          {saving ? '保存中...' : '保存'}
        </button>
        {/* 提示信息 */}
        {message && (
          <div style={{ color: 'var(--success)', fontSize: '12px' }}>{message}</div>
        )}
        {error && <div style={{ color: 'var(--error)', fontSize: '12px' }}>{error}</div>}
      </div>
    </div>
  )
}

export default SettingsPanel
