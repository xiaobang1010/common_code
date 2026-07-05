// LLM 模型设置区 — base_url/api_key/model 编辑 + 供应商切换
// 迁移自旧 SettingsPanel，改用统一 API 客户端和原子组件
// 保存后通过 store 广播，让 StatusBar 刷新 model 显示

import { useEffect, useState } from 'react'
import { llmApi } from '../../api/client'
import { useSettingsStore } from '../../stores/useSettingsStore'
import { TextInput, Select, SettingSection, SettingRow, StatusMessage } from '../ui'

function LLMSettingsSection() {
  const { providers, activeProvider, refreshLlmConfig, refreshProviders, notifyModelChanged } =
    useSettingsStore()

  const [baseUrl, setBaseUrl] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [model, setModel] = useState('')
  const [showKey, setShowKey] = useState(false)
  const [saving, setSaving] = useState(false)
  const [switching, setSwitching] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  // 初次加载配置
  useEffect(() => {
    const load = async () => {
      try {
        const config = await llmApi.getConfig()
        setBaseUrl(config.llm_base_url || '')
        setApiKey(config.llm_api_key || '')
        setModel(config.llm_model || '')
      } catch (e) {
        setError(`加载配置失败：${e instanceof Error ? e.message : String(e)}`)
      }
    }
    load()
  }, [])

  // 保存基础 LLM 配置
  const handleSave = async () => {
    setSaving(true)
    setError('')
    setMessage('')
    try {
      await llmApi.saveConfig({
        llm_base_url: baseUrl,
        llm_api_key: apiKey,
        llm_model: model,
      })
      setMessage('配置已保存，重启后完全生效')
      // 刷新 store 并广播，让 StatusBar 的 model 更新
      await refreshLlmConfig()
      notifyModelChanged()
      setTimeout(() => setMessage(''), 3000)
    } catch (e) {
      setError(e instanceof Error ? e.message : '保存失败')
    } finally {
      setSaving(false)
    }
  }

  // 切换 LLM 供应商
  const handleSwitchProvider = async (name: string) => {
    setSwitching(true)
    setError('')
    setMessage('')
    try {
      await llmApi.switchProvider(name)
      setMessage(`已切换到供应商：${name}`)
      await refreshProviders()
      notifyModelChanged()
      setTimeout(() => setMessage(''), 3000)
    } catch (e) {
      setError(e instanceof Error ? e.message : '切换失败')
    } finally {
      setSwitching(false)
    }
  }

  return (
    <div>
      <SettingSection
        title="基础配置"
        description="LLM 服务的连接参数。环境变量和激活的供应商会优先于这里的配置。"
      >
        <SettingRow label="Base URL">
          <div style={{ flex: 1, minWidth: '300px' }}>
            <TextInput
              value={baseUrl}
              onChange={setBaseUrl}
              placeholder="https://api.example.com/v1"
            />
          </div>
        </SettingRow>

        <SettingRow label="API Key" description="API 密钥，保存后生效">
          <div style={{ display: 'flex', gap: '4px', flex: 1, minWidth: '300px' }}>
            <TextInput
              type={showKey ? 'text' : 'password'}
              value={apiKey}
              onChange={setApiKey}
              placeholder="sk-..."
            />
            <button
              onClick={() => setShowKey(!showKey)}
              title={showKey ? '隐藏' : '显示'}
              style={{
                border: '1px solid var(--border)',
                backgroundColor: 'var(--bg-primary)',
                color: 'var(--text-secondary)',
                width: '32px',
                cursor: 'pointer',
                fontSize: '14px',
                borderRadius: 'var(--radius-sm)',
                flexShrink: 0,
              }}
            >
              {showKey ? '🙈' : '👁'}
            </button>
          </div>
        </SettingRow>

        <SettingRow label="Model">
          <div style={{ flex: 1, minWidth: '300px' }}>
            <TextInput
              value={model}
              onChange={setModel}
              placeholder="gpt-4o"
            />
          </div>
        </SettingRow>

        <button
          onClick={handleSave}
          disabled={saving}
          style={{
            marginTop: '16px',
            padding: '8px 20px',
            border: 'none',
            borderRadius: 'var(--radius-sm)',
            backgroundColor: saving ? 'var(--bg-tertiary)' : 'var(--accent)',
            color: saving ? 'var(--text-tertiary)' : '#1a1a1a',
            cursor: saving ? 'not-allowed' : 'pointer',
            fontSize: '13px',
            fontWeight: 600,
            fontFamily: 'var(--font-ui)',
          }}
        >
          {saving ? '保存中...' : '保存配置'}
        </button>
      </SettingSection>

      {providers.length > 0 && (
        <SettingSection
          title="LLM 供应商"
          description="切换由 llm-provider 插件提供的模型后端。切换后立即生效，无需重启。"
        >
          <SettingRow
            label="当前供应商"
            description={`已安装 ${providers.length} 个供应商插件`}
          >
            <Select
              value={activeProvider || ''}
              onChange={handleSwitchProvider}
              disabled={switching}
              options={providers.map((p) => ({
                value: p.name,
                label: `${p.name} (${p.model})`,
              }))}
            />
          </SettingRow>
        </SettingSection>
      )}

      <StatusMessage type="success" message={message} />
      <StatusMessage type="error" message={error} />
    </div>
  )
}

export default LLMSettingsSection
