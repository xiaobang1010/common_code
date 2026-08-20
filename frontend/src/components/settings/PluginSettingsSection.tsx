// 插件管理区 — 列表展示 + 启停 + 来源和能力数量
// 迁移自旧 SettingsPanel，改用统一 API 客户端和原子组件

import { useEffect, useState } from 'react'
import { pluginsApi } from '../../api/client'
import { useSettingsStore } from '../../stores/useSettingsStore'
import { Toggle, StatusMessage } from '../ui'
import type { PluginInfo } from '../../api/client'

// 来源标签的颜色映射
const sourceStyle: Record<string, { bg: string; color: string; label: string }> = {
  bundled: { bg: 'rgba(108, 182, 255, 0.12)', color: 'var(--info)', label: '内置' },
  user: { bg: 'rgba(255, 255, 255, 0.08)', color: 'var(--text-secondary)', label: '用户' },
  project: { bg: 'rgba(78, 201, 176, 0.12)', color: 'var(--success)', label: '项目' },
}

function PluginSettingsSection() {
  const { refreshPlugins } = useSettingsStore()
  const [plugins, setPlugins] = useState<PluginInfo[]>([])
  const [loading, setLoading] = useState(true)
  const [toggling, setToggling] = useState<string | null>(null)
  const [error, setError] = useState('')

  const load = async () => {
    try {
      const data = await pluginsApi.list()
      setPlugins(data.plugins)
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载插件失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
  }, [])

  // 启停某个插件
  const handleToggle = async (plugin: PluginInfo) => {
    setToggling(plugin.name)
    setError('')
    try {
      if (plugin.enabled) {
        await pluginsApi.disable(plugin.name)
      } else {
        await pluginsApi.enable(plugin.name)
      }
      // 刷新本地列表和 store
      await load()
      await refreshPlugins()
    } catch (e) {
      setError(e instanceof Error ? e.message : '操作失败')
    } finally {
      setToggling(null)
    }
  }

  if (loading) {
    return <div style={{ color: 'var(--text-secondary)', fontSize: '13px' }}>加载中...</div>
  }

  if (plugins.length === 0) {
    return (
      <div style={{ padding: '40px 20px', textAlign: 'center', color: 'var(--text-tertiary)' }}>
        <div style={{ fontSize: '32px', marginBottom: '12px' }}>📦</div>
        <div style={{ fontSize: '13px' }}>未发现任何插件</div>
        <div style={{ fontSize: '12px', marginTop: '8px', lineHeight: 1.6 }}>
          把插件目录放到 <code style={{ color: 'var(--code-text)', fontFamily: 'var(--font-mono)' }}>~/.agent/plugins/</code>
          或项目 <code style={{ color: 'var(--code-text)', fontFamily: 'var(--font-mono)' }}>.agent/plugins/</code> 下即可
        </div>
      </div>
    )
  }

  return (
    <div>
      <div
        style={{
          fontSize: '13px',
          color: 'var(--text-primary)',
          fontWeight: 600,
          marginBottom: '16px',
        }}
      >
        已安装插件（{plugins.length}）
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
        {plugins.map((p) => {
          const src = sourceStyle[p.source || 'user'] || sourceStyle.user
          return (
            <div
              key={p.name}
              style={{
                padding: '12px 14px',
                backgroundColor: 'var(--bg-primary)',
                border: '1px solid var(--border)',
                borderRadius: 'var(--radius-md)',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: '12px' }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '4px' }}>
                    <span style={{ color: 'var(--text-primary)', fontWeight: 600, fontSize: '13px' }}>
                      {p.name}
                    </span>
                    <span style={{ color: 'var(--text-tertiary)', fontSize: '11px' }}>
                      v{p.version}
                    </span>
                    {/* 来源标签 */}
                    <span
                      style={{
                        backgroundColor: src.bg,
                        color: src.color,
                        fontSize: '10px',
                        padding: '1px 6px',
                        borderRadius: 'var(--radius-sm)',
                        fontWeight: 500,
                      }}
                    >
                      {src.label}
                    </span>
                    {/* kind 标签 */}
                    <span
                      style={{
                        color: 'var(--text-tertiary)',
                        fontSize: '10px',
                        padding: '1px 6px',
                        border: '1px solid var(--border)',
                        borderRadius: 'var(--radius-sm)',
                      }}
                    >
                      {p.kind}
                    </span>
                  </div>
                  {p.description && (
                    <div style={{ color: 'var(--text-secondary)', fontSize: '12px', marginBottom: '6px' }}>
                      {p.description}
                    </div>
                  )}
                  {/* 已注册能力数量 */}
                  <div style={{ display: 'flex', gap: '12px', fontSize: '11px', color: 'var(--text-tertiary)' }}>
                    {(p.skills_count || 0) > 0 && <span>skills: {p.skills_count}</span>}
                    {(p.hooks_count || 0) > 0 && <span>hooks: {p.hooks_count}</span>}
                    {(p.commands_count || 0) > 0 && <span>commands: {p.commands_count}</span>}
                    {(p.mcp_servers_count || 0) > 0 && <span>mcp: {p.mcp_servers_count}</span>}
                  </div>
                </div>
                <Toggle
                  checked={p.enabled}
                  onChange={() => handleToggle(p)}
                  disabled={toggling === p.name}
                  loading={toggling === p.name}
                />
              </div>
            </div>
          )
        })}
      </div>

      {error ? (
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginTop: '12px' }}>
          <span style={{ fontSize: '12px', color: 'var(--error)' }}>{error}</span>
          <button
            onClick={() => void load()}
            style={{ cursor: 'pointer', background: 'transparent', border: '1px solid var(--border)', color: 'var(--text-secondary)', borderRadius: 'var(--radius-sm)', fontSize: '12px', padding: '2px 10px' }}
          >
            重试
          </button>
        </div>
      ) : null}
    </div>
  )
}

export default PluginSettingsSection
