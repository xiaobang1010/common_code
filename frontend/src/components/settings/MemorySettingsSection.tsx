// 记忆管理区 - 列出后端 + 切换激活 + 清空会话记忆
// 接 GET /api/memory/providers、POST /api/memory/switch、POST /api/memory/clear

import { useEffect, useState } from 'react'
import { memoryApi } from '../../api/client'
import { useSettingsStore } from '../../stores/useSettingsStore'
import { Select, SettingSection, SettingRow, StatusMessage, Toggle } from '../ui'

function KGEntities() {
  const [entities, setEntities] = useState<string[]>([])
  const [loading, setLoading] = useState(false)
  const [collapsed, setCollapsed] = useState(true)
  const [error, setError] = useState('')

  const fetchEntities = async () => {
    setLoading(true)
    setError('')
    try {
      const res = await memoryApi.kgEntities()
      setEntities(res.entities || [])
    } catch (e) {
      setError(e instanceof Error ? e.message : '获取实体失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (!collapsed && entities.length === 0 && !loading) {
      fetchEntities()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [collapsed])

  return (
    <div style={{ padding: '8px 0' }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '8px',
          cursor: 'pointer',
          userSelect: 'none',
        }}
        onClick={() => setCollapsed(!collapsed)}
      >
        <span style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
          {collapsed ? '▶' : '▼'}
        </span>
        <span style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>
          实体列表 ({entities.length})
        </span>
        {!collapsed && (
          <button
            onClick={(e) => {
              e.stopPropagation()
              fetchEntities()
            }}
            style={{
              marginLeft: 'auto',
              padding: '2px 8px',
              fontSize: '11px',
              border: '1px solid var(--border)',
              borderRadius: 'var(--radius-sm)',
              backgroundColor: 'transparent',
              color: 'var(--text-secondary)',
              cursor: 'pointer',
              fontFamily: 'var(--font-ui)',
            }}
          >
            刷新
          </button>
        )}
      </div>
      {!collapsed && (
        <div style={{ marginTop: '8px', marginLeft: '20px' }}>
          {loading && (
            <div style={{ fontSize: '12px', color: 'var(--text-tertiary)' }}>
              加载中...
            </div>
          )}
          {error && (
            <div style={{ fontSize: '12px', color: 'var(--error)' }}>
              {error}
            </div>
          )}
          {!loading && !error && entities.length === 0 && (
            <div style={{ fontSize: '12px', color: 'var(--text-tertiary)' }}>
              暂无实体
            </div>
          )}
          {entities.length > 0 && (
            <div
              style={{
                display: 'flex',
                flexWrap: 'wrap',
                gap: '6px',
              }}
            >
              {entities.map((entity) => (
                <span
                  key={entity}
                  style={{
                    padding: '2px 8px',
                    fontSize: '12px',
                    fontFamily: 'var(--font-mono)',
                    border: '1px solid var(--border)',
                    borderRadius: 'var(--radius-sm)',
                    color: 'var(--text-secondary)',
                    backgroundColor: 'var(--bg-primary)',
                  }}
                >
                  {entity}
                </span>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function MemorySettingsSection() {
  const { memoryProviders, activeMemory, memoryEnabled, setMemoryEnabled, refreshMemoryProviders } = useSettingsStore()
  const [switching, setSwitching] = useState(false)
  const [clearing, setClearing] = useState(false)
  const [featureBusy, setFeatureBusy] = useState(false)
  const [sessionId, setSessionId] = useState('default')
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [palaceStatus, setPalaceStatus] = useState<any>(null)

  useEffect(() => {
    refreshMemoryProviders()
  }, [refreshMemoryProviders])

  // 挂载时同步记忆功能开关状态（后端 memoryEnabled 的前端镜像）
  useEffect(() => {
    memoryApi.feature()
      .then((f) => setMemoryEnabled(f.enabled))
      .catch(() => {})
  }, [setMemoryEnabled])

  useEffect(() => {
    if (activeMemory === 'memory-palace') {
      memoryApi.status().then(setPalaceStatus).catch(() => setPalaceStatus(null))
    } else {
      setPalaceStatus(null)
    }
  }, [activeMemory, memoryProviders])

  // 切换记忆功能开关：持久化 + 即时生效，刷新后端列表（两方向都刷新）
  const handleToggleFeature = async (enabled: boolean) => {
    setFeatureBusy(true)
    setError('')
    setMessage('')
    try {
      await memoryApi.setFeature(enabled)
      setMemoryEnabled(enabled)
      await refreshMemoryProviders()
      setMessage(enabled ? '记忆功能已开启，模型后台加载中' : '记忆功能已关闭')
      setTimeout(() => setMessage(''), 3000)
    } catch (e) {
      setError(e instanceof Error ? e.message : '切换失败')
    } finally {
      setFeatureBusy(false)
    }
  }

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

  // 记忆功能开关行（空状态之前渲染，关闭时也能操作）
  const featureSwitch = (
    <SettingSection
      title="记忆功能"
      description="控制记忆功能的启用状态。关闭时启动不加载向量化模型，省内存省启动时间。"
    >
      <SettingRow label="启用记忆功能" description="开启后向量模型后台异步加载，不影响其他功能">
        <Toggle checked={memoryEnabled} onChange={handleToggleFeature} loading={featureBusy} />
      </SettingRow>
    </SettingSection>
  )

  // 空状态：无记忆后端（默认关闭时后端未注册，需区分"功能关闭"与"未安装"）
  if (memoryProviders.length === 0) {
    return (
      <div>
        {featureSwitch}
        <div style={{ padding: '24px 20px', textAlign: 'center', color: 'var(--text-tertiary)' }}>
          <div style={{ fontSize: '32px', marginBottom: '12px' }}>🧠</div>
          <div style={{ fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '8px' }}>
            {memoryEnabled ? '未安装任何记忆后端' : '记忆功能已关闭'}
          </div>
          <div style={{ fontSize: '12px', lineHeight: 1.6, maxWidth: '420px', margin: '0 auto' }}>
            {memoryEnabled ? (
              <>
                记忆插件是 <code style={{ color: 'var(--code-text)', fontFamily: 'var(--font-mono)' }}>memory</code> kind 的插件，
                在插件目录放 <code style={{ color: 'var(--code-text)', fontFamily: 'var(--font-mono)' }}>memory.py</code>
                并实现 store/retrieve/search/clear 四个方法即可。
                没有记忆后端时，对话照常运行，只是不会跨会话保留摘要。
              </>
            ) : (
              '开启后自动加载记忆后端，向量模型将在后台异步加载，不影响其他功能。'
            )}
          </div>
        </div>
        <StatusMessage type="success" message={message} />
        <StatusMessage type="error" message={error} />
      </div>
    )
  }

  return (
    <div>
      {featureSwitch}
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

      {/* Palace 状态 */}
      {palaceStatus && palaceStatus.ok && (
        <SettingSection
          title="Palace 状态"
          description="记忆宫殿的当前状态和分布"
        >
          <div style={{ padding: '8px 0' }}>
            <div style={{ display: 'flex', gap: '24px', marginBottom: '16px' }}>
              <div>
                <div style={{ fontSize: '24px', fontWeight: 600, color: 'var(--text-primary)' }}>
                  {palaceStatus.status.total_drawers}
                </div>
                <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>总抽屉数</div>
              </div>
              <div>
                <div style={{ fontSize: '24px', fontWeight: 600, color: 'var(--text-primary)' }}>
                  {palaceStatus.status.total_wings}
                </div>
                <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>Wing 数</div>
              </div>
            </div>
            {palaceStatus.status.wings && palaceStatus.status.wings.length > 0 && (
              <div style={{ fontSize: '12px', fontFamily: 'var(--font-mono)', color: 'var(--text-secondary)' }}>
                {palaceStatus.status.wings.map((w: any) => (
                  <div key={w.name} style={{ marginBottom: '4px' }}>
                    <span style={{ color: 'var(--text-primary)' }}>{w.name}</span>
                    {' '}
                    <span style={{ color: 'var(--text-tertiary)' }}>({w.drawer_count})</span>
                    {w.rooms && w.rooms.length > 0 && (
                      <div style={{ marginLeft: '16px', marginTop: '2px' }}>
                        {w.rooms.map((r: any) => (
                          <div key={r.name}>
                            <span style={{ color: 'var(--text-secondary)' }}>{r.name}</span>
                            {' '}
                            <span style={{ color: 'var(--text-tertiary)' }}>({r.drawer_count})</span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        </SettingSection>
      )}

      {/* 知识图谱实体 */}
      {activeMemory === 'memory-palace' && (
        <SettingSection
          title="知识图谱"
          description="记忆宫殿中的实体关系图谱"
        >
          <KGEntities />
        </SettingSection>
      )}

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
