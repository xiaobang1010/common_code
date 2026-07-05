// 记忆管理区 — 列出后端 + 切换激活 + 清空会话记忆
// 接 GET /api/memory/providers、POST /api/memory/switch、POST /api/memory/clear

import { useEffect, useState } from 'react'
import { memoryApi } from '../../api/client'
import { useSettingsStore } from '../../stores/useSettingsStore'
import { Select, SettingSection, SettingRow, StatusMessage } from '../ui'

function MemorySettingsSection() {
  const { memoryProviders, activeMemory, refreshMemoryProviders } = useSettingsStore()
  const [switching, setSwitching] = useState(false)
  const [clearing, setClearing] = useState(false)
  const [sessionId, setSessionId] = useState('default')
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    refreshMemoryProviders()
  }, [refreshMemoryProviders])

  // 切换激活记忆后端
  const handleSwitch = async (name: string) => {
    setSwitching(true)
    setError('')
    setMessage('')
    try {
      await memoryApi.switch(name)
      setMessage(`已切换到：${name}`)
      await refreshMemoryProviders()
      setTimeout(() => setMessage(''), 3000)
    } catch (e) {
      setError(e instanceof Error ? e.message : '切换失败')
    } finally {
      setSwitching(false)
    }
  }

  // 清空指定会话记忆
  const handleClear = async () => {
    if (!activeMemory) return
    if (!confirm(`确认清空会话 "${sessionId}" 的记忆？此操作不可撤销。`)) return
    setClearing(true)
    setError('')
    setMessage('')
    try {
      await memoryApi.clear(sessionId)
      setMessage('记忆已清空')
      setTimeout(() => setMessage(''), 3000)
    } catch (e) {
      setError(e instanceof Error ? e.message : '清空失败')
    } finally {
      setClearing(false)
    }
  }

  // 空状态：无记忆后端
  if (memoryProviders.length === 0) {
    return (
      <div style={{ padding: '40px 20px', textAlign: 'center', color: 'var(--text-tertiary)' }}>
        <div style={{ fontSize: '32px', marginBottom: '12px' }}>🧠</div>
        <div style={{ fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '8px' }}>
          未安装任何记忆后端
        </div>
        <div style={{ fontSize: '12px', lineHeight: 1.6, maxWidth: '420px', margin: '0 auto' }}>
          记忆插件是 <code style={{ color: 'var(--accent)', fontFamily: 'var(--font-mono)' }}>memory</code> kind 的插件，
          在插件目录放 <code style={{ color: 'var(--accent)', fontFamily: 'var(--font-mono)' }}>memory.py</code>
          并实现 store/retrieve/search/clear 四个方法即可。
          没有记忆后端时，对话照常运行，只是不会跨会话保留摘要。
        </div>
      </div>
    )
  }

  return (
    <div>
      <SettingSection
        title="记忆后端"
        description="管理记忆存储后端。同一时间只有一个后端激活，切换后立即生效并持久化。"
      >
        <SettingRow
          label="当前激活"
          description={`已安装 ${memoryProviders.length} 个记忆后端`}
        >
          <Select
            value={activeMemory || ''}
            onChange={handleSwitch}
            disabled={switching}
            options={memoryProviders.map((p) => ({
              value: p.name,
              label: p.name,
            }))}
          />
        </SettingRow>
      </SettingSection>

      <SettingSection
        title="清空记忆"
        description="清空指定会话的所有记忆。此操作不可撤销，请谨慎。"
      >
        <SettingRow label="会话 ID">
          <div style={{ flex: 1, minWidth: '200px' }}>
            <input
              value={sessionId}
              onChange={(e) => setSessionId(e.target.value)}
              style={{
                width: '100%',
                boxSizing: 'border-box',
                backgroundColor: 'var(--bg-primary)',
                border: '1px solid var(--border)',
                color: 'var(--text-primary)',
                padding: '6px 10px',
                fontSize: '13px',
                borderRadius: 'var(--radius-sm)',
                fontFamily: 'var(--font-mono)',
              }}
            />
          </div>
        </SettingRow>

        <button
          onClick={handleClear}
          disabled={clearing || !activeMemory}
          style={{
            marginTop: '12px',
            padding: '8px 16px',
            border: '1px solid var(--error)',
            borderRadius: 'var(--radius-sm)',
            backgroundColor: 'transparent',
            color: 'var(--error)',
            cursor: clearing || !activeMemory ? 'not-allowed' : 'pointer',
            fontSize: '13px',
            fontFamily: 'var(--font-ui)',
            opacity: clearing || !activeMemory ? 0.5 : 1,
          }}
        >
          {clearing ? '清空中...' : '清空记忆'}
        </button>
      </SettingSection>

      <StatusMessage type="success" message={message} />
      <StatusMessage type="error" message={error} />
    </div>
  )
}

export default MemorySettingsSection
