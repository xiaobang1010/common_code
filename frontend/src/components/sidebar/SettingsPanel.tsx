import { useEffect, useState } from 'react'

// LLM 配置结构
interface LLMConfig {
  llm_base_url: string
  llm_api_key: string
  llm_model: string
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

  // 加载当前配置
  useEffect(() => {
    const loadConfig = async () => {
      try {
        const res = await fetch('/api/config')
        const json = await res.json()
        setBaseUrl(json.llm_base_url || '')
        setApiKey(json.llm_api_key || '')
        setModel(json.llm_model || '')
      } catch (e) {
        setError('加载配置失败')
        console.error(e)
      } finally {
        setLoading(false)
      }
    }
    loadConfig()
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
