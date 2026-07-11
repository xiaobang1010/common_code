// LLM 模型设置区 - 自定义供应商列表管理（增删改查 + 测试连接 + 激活）
// 接 /api/llm-providers 系列接口，支持多供应商多模型的完整管理
// 保存/删除/激活后通过 store 广播，让 StatusBar 刷新 model 显示

import { useEffect, useState, useCallback } from 'react'
import { llmApi } from '../../api/client'
import type {
  CustomLLMProviderInfo,
  CustomLLMModelInfo,
  ApiFormat,
} from '../../api/client'
import { useSettingsStore } from '../../stores/useSettingsStore'
import { TextInput, Select, StatusMessage } from '../ui'

// ---------------------------------------------------------------------------
// 类型与常量
// ---------------------------------------------------------------------------

// 编辑表单的数据结构（去掉 id，保存时按需 create/update）
interface ProviderFormData {
  name: string
  base_url: string
  api_key: string
  api_format: ApiFormat
  models: CustomLLMModelInfo[]
}

// 空表单初始值
const emptyForm: ProviderFormData = {
  name: '',
  base_url: '',
  api_key: '',
  api_format: 'openai',
  models: [],
}

// API 格式标签配置（标签 + 后缀说明）
const apiFormatLabel: Record<ApiFormat, string> = {
  openai: 'OpenAI',
  anthropic: 'Anthropic',
}

// Base URL 后缀说明
const apiFormatSuffixHint: Record<ApiFormat, string> = {
  openai: ' + /chat/completions',
  anthropic: ' + /v1/messages',
}

// ---------------------------------------------------------------------------
// 主组件
// ---------------------------------------------------------------------------

function LLMSettingsSection() {
  const { refreshLlmConfig, refreshProviders, notifyModelChanged } =
    useSettingsStore()

  // 供应商列表与激活状态（来自 listCustomProviders）
  const [providers, setProviders] = useState<CustomLLMProviderInfo[]>([])
  const [activeProvider, setActiveProvider] = useState<string | null>(null)
  const [activeModel, setActiveModel] = useState<string | null>(null)

  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')

  // 编辑/新建表单：form 有值时显示弹窗
  // editingId 为 null 表示新建，有值表示编辑对应 id 的供应商
  const [form, setForm] = useState<ProviderFormData | null>(null)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  // 测试连接：记录正在测试的供应商 id
  const [testingId, setTestingId] = useState<string | null>(null)
  // 测试结果：供应商 id -> { ok, message }
  const [testResults, setTestResults] = useState<
    Record<string, { ok: boolean; message: string }>
  >({})

  // 激活中：记录正在激活的 "providerId:modelId"
  const [activating, setActivating] = useState<string | null>(null)

  // 加载供应商列表
  const load = useCallback(async () => {
    try {
      const data = await llmApi.listCustomProviders()
      setProviders(data.providers)
      setActiveProvider(data.active_provider)
      setActiveModel(data.active_model)
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载供应商失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  // 点击"添加供应商"
  const handleAdd = () => {
    setForm({ ...emptyForm, models: [] })
    setEditingId(null)
    setError('')
    setSuccess('')
  }

  // 点击"编辑"
  const handleEdit = (p: CustomLLMProviderInfo) => {
    setForm({
      name: p.name,
      base_url: p.base_url,
      api_key: p.api_key,
      api_format: p.api_format,
      models: p.models.map((m) => ({ ...m })),
    })
    setEditingId(p.id)
    setError('')
    setSuccess('')
  }

  // 取消编辑
  const handleCancel = () => {
    setForm(null)
    setEditingId(null)
  }

  // 保存（新建或更新）
  const handleSave = async () => {
    if (!form) return
    if (!form.name.trim()) {
      setError('供应商名称不能为空')
      return
    }
    if (!form.base_url.trim()) {
      setError('Base URL 不能为空')
      return
    }
    setSaving(true)
    setError('')
    try {
      // 组装提交数据，过滤掉没有 model_id 的空行
      const payload = {
        name: form.name.trim(),
        base_url: form.base_url.trim(),
        api_key: form.api_key.trim(),
        api_format: form.api_format,
        models: form.models
          .filter((m) => m.model_id.trim())
          .map((m) => ({
            model_id: m.model_id.trim(),
            context_window: Number(m.context_window) || 0,
          })),
      }
      if (editingId) {
        await llmApi.updateProvider(editingId, payload)
        setSuccess('供应商已更新')
      } else {
        await llmApi.createProvider(payload)
        setSuccess('供应商已创建')
      }
      setForm(null)
      setEditingId(null)
      // 刷新本地列表 + store，再广播让 StatusBar 更新
      await load()
      await refreshLlmConfig()
      await refreshProviders()
      notifyModelChanged()
      setTimeout(() => setSuccess(''), 3000)
    } catch (e) {
      setError(e instanceof Error ? e.message : '保存失败')
    } finally {
      setSaving(false)
    }
  }

  // 删除供应商
  const handleDelete = async (p: CustomLLMProviderInfo) => {
    if (!confirm(`确认删除供应商「${p.name}」？此操作不可撤销。`)) return
    setError('')
    setSuccess('')
    try {
      await llmApi.deleteProvider(p.id)
      setSuccess(`已删除供应商：${p.name}`)
      await load()
      await refreshLlmConfig()
      await refreshProviders()
      notifyModelChanged()
      setTimeout(() => setSuccess(''), 3000)
    } catch (e) {
      setError(e instanceof Error ? e.message : '删除失败')
    }
  }

  // 测试连接
  const handleTest = async (p: CustomLLMProviderInfo) => {
    setTestingId(p.id)
    // 清掉该供应商旧的测试结果
    setTestResults((prev) => {
      const next = { ...prev }
      delete next[p.id]
      return next
    })
    try {
      const res = await llmApi.testProvider(p.id)
      setTestResults((prev) => ({
        ...prev,
        [p.id]: { ok: true, message: res.message || '连接成功' },
      }))
    } catch (e) {
      setTestResults((prev) => ({
        ...prev,
        [p.id]: { ok: false, message: e instanceof Error ? e.message : '连接失败' },
      }))
    } finally {
      setTestingId(null)
    }
  }

  // 激活供应商 + 模型
  const handleActivate = async (providerId: string, modelId: string) => {
    const key = `${providerId}:${modelId}`
    setActivating(key)
    setError('')
    setSuccess('')
    try {
      await llmApi.activateProvider(providerId, modelId)
      setSuccess(`已激活模型：${modelId}`)
      await load()
      await refreshLlmConfig()
      await refreshProviders()
      notifyModelChanged()
      setTimeout(() => setSuccess(''), 3000)
    } catch (e) {
      setError(e instanceof Error ? e.message : '激活失败')
    } finally {
      setActivating(null)
    }
  }

  // -----------------------------------------------------------------------
  // 渲染
  // -----------------------------------------------------------------------

  if (loading) {
    return (
      <div style={{ color: 'var(--text-secondary)', fontSize: '13px' }}>
        加载中...
      </div>
    )
  }

  return (
    <div>
      {/* 顶部操作栏：标题 + 添加按钮 */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          marginBottom: '16px',
        }}
      >
        <div style={{ fontSize: '13px', color: 'var(--text-primary)', fontWeight: 600 }}>
          LLM 供应商（{providers.length}）
        </div>
        <button onClick={handleAdd} style={btnStyle('primary')}>
          + 添加供应商
        </button>
      </div>

      {/* 供应商卡片列表 */}
      {providers.length === 0 ? (
        <div
          style={{
            padding: '40px 20px',
            textAlign: 'center',
            color: 'var(--text-tertiary)',
          }}
        >
          <div style={{ fontSize: '32px', marginBottom: '12px' }}>🔌</div>
          <div style={{ fontSize: '13px' }}>还没有配置任何 LLM 供应商</div>
          <div style={{ fontSize: '12px', marginTop: '8px', lineHeight: 1.6 }}>
            点击右上角"添加供应商"开始配置你的第一个模型后端
          </div>
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
          {providers.map((p) => (
            <ProviderCard
              key={p.id}
              provider={p}
              isActiveProvider={activeProvider === p.id}
              activeModel={activeModel}
              testing={testingId === p.id}
              testResult={testResults[p.id]}
              activating={activating}
              onEdit={() => handleEdit(p)}
              onTest={() => handleTest(p)}
              onDelete={() => handleDelete(p)}
              onActivate={(modelId) => handleActivate(p.id, modelId)}
            />
          ))}
        </div>
      )}

      <StatusMessage type="success" message={success} />
      <StatusMessage type="error" message={error} />

      {/* 编辑/新建弹窗 */}
      {form && (
        <ProviderEditModal
          form={form}
          setForm={setForm}
          editing={editingId !== null}
          saving={saving}
          error={error}
          onSave={handleSave}
          onCancel={handleCancel}
        />
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// ProviderCard - 供应商卡片
// ---------------------------------------------------------------------------

interface ProviderCardProps {
  provider: CustomLLMProviderInfo
  isActiveProvider: boolean
  activeModel: string | null
  testing: boolean
  testResult?: { ok: boolean; message: string }
  activating: string | null
  onEdit: () => void
  onTest: () => void
  onDelete: () => void
  onActivate: (modelId: string) => void
}

function ProviderCard({
  provider,
  isActiveProvider,
  activeModel,
  testing,
  testResult,
  activating,
  onEdit,
  onTest,
  onDelete,
  onActivate,
}: ProviderCardProps) {
  const p = provider
  return (
    <div
      style={{
        padding: '12px 14px',
        backgroundColor: 'var(--bg-primary)',
        border: '1px solid var(--border)',
        borderRadius: 'var(--radius-md)',
        // 激活的供应商左侧加绿色边框
        borderLeft: isActiveProvider
          ? '3px solid var(--success)'
          : '1px solid var(--border)',
      }}
    >
      {/* 标题行：名称 + 标签 + 激活角标 */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '8px',
          marginBottom: '8px',
        }}
      >
        <span
          style={{
            color: 'var(--text-primary)',
            fontWeight: 600,
            fontSize: '13px',
            flex: 1,
          }}
        >
          {p.name}
        </span>
        {/* API 格式标签（含后缀说明） */}
        <span style={tagStyle('rgba(108, 182, 255, 0.12)', 'var(--info)')} title={`请求路径：Base URL${apiFormatSuffixHint[p.api_format]}`}>
          {apiFormatLabel[p.api_format]}{apiFormatSuffixHint[p.api_format]}
        </span>
        {/* 模型数量标签 */}
        <span
          style={tagStyle('rgba(160, 160, 160, 0.1)', 'var(--text-tertiary)')}
        >
          {p.models.length} 个模型
        </span>
        {/* 激活角标 */}
        {isActiveProvider && (
          <span
            style={tagStyle('rgba(78, 201, 176, 0.12)', 'var(--success)')}
          >
            ● 已激活
          </span>
        )}
      </div>

      {/* Base URL 显示 */}
      <div
        style={{
          fontSize: '11px',
          color: 'var(--text-tertiary)',
          fontFamily: 'var(--font-mono)',
          marginBottom: '8px',
          wordBreak: 'break-all',
        }}
      >
        {p.base_url}
      </div>

      {/* 模型列表 */}
      {p.models.length > 0 && (
        <div
          style={{
            display: 'flex',
            flexDirection: 'column',
            gap: '4px',
            marginBottom: '10px',
          }}
        >
          {p.models.map((m) => {
            const isThisActive =
              isActiveProvider && activeModel === m.model_id
            const activateKey = `${p.id}:${m.model_id}`
            const isActivating = activating === activateKey
            return (
              <div
                key={m.model_id}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '8px',
                  padding: '4px 8px',
                  backgroundColor: isThisActive
                    ? 'rgba(78, 201, 176, 0.06)'
                    : 'var(--bg-tertiary)',
                  borderRadius: 'var(--radius-sm)',
                }}
              >
                <span
                  style={{
                    color: isThisActive
                      ? 'var(--success)'
                      : 'var(--text-secondary)',
                    fontSize: '12px',
                    fontFamily: 'var(--font-mono)',
                    flex: 1,
                  }}
                >
                  {m.model_id}
                </span>
                <span
                  style={{
                    color: 'var(--text-tertiary)',
                    fontSize: '11px',
                    flexShrink: 0,
                  }}
                >
                  {m.context_window > 0
                    ? `${(m.context_window / 1000).toFixed(0)}K ctx`
                    : '-'}
                </span>
                {isThisActive ? (
                  <span
                    style={{
                      color: 'var(--success)',
                      fontSize: '11px',
                      flexShrink: 0,
                    }}
                  >
                    当前模型
                  </span>
                ) : (
                  <button
                    onClick={() => onActivate(m.model_id)}
                    disabled={isActivating}
                    style={{
                      border: 'none',
                      background: 'transparent',
                      color: isActivating
                        ? 'var(--text-tertiary)'
                        : 'var(--accent)',
                      cursor: isActivating ? 'not-allowed' : 'pointer',
                      fontSize: '11px',
                      padding: '2px 6px',
                      flexShrink: 0,
                      opacity: isActivating ? 0.5 : 1,
                    }}
                  >
                    {isActivating ? '...' : '激活'}
                  </button>
                )}
              </div>
            )
          })}
        </div>
      )}

      {/* 测试结果提示 */}
      {testResult && (
        <div
          style={{
            fontSize: '11px',
            padding: '4px 8px',
            marginBottom: '8px',
            borderRadius: 'var(--radius-sm)',
            color: testResult.ok ? 'var(--success)' : 'var(--error)',
            backgroundColor: testResult.ok
              ? 'rgba(78, 201, 176, 0.08)'
              : 'rgba(255, 107, 107, 0.08)',
          }}
        >
          {testResult.ok ? '✓ ' : '✗ '}
          {testResult.message}
        </div>
      )}

      {/* 操作按钮行 */}
      <div style={{ display: 'flex', gap: '8px' }}>
        <button onClick={onEdit} style={btnStyle('default')}>
          编辑
        </button>
        <button
          onClick={onTest}
          disabled={testing}
          style={{
            ...btnStyle('default'),
            opacity: testing ? 0.5 : 1,
            cursor: testing ? 'not-allowed' : 'pointer',
          }}
        >
          {testing ? '测试中...' : '测试连接'}
        </button>
        <button
          onClick={onDelete}
          style={{
            ...btnStyle('danger'),
            marginLeft: 'auto',
          }}
        >
          删除
        </button>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// ProviderEditModal - 供应商编辑/新建弹窗
// ---------------------------------------------------------------------------

interface ProviderEditModalProps {
  form: ProviderFormData
  setForm: (f: ProviderFormData) => void
  editing: boolean
  saving: boolean
  error: string
  onSave: () => void
  onCancel: () => void
}

function ProviderEditModal({
  form,
  setForm,
  editing,
  saving,
  error,
  onSave,
  onCancel,
}: ProviderEditModalProps) {
  const [showKey, setShowKey] = useState(false)

  // 更新表单中某个字段
  const updateField = <K extends keyof ProviderFormData>(
    key: K,
    value: ProviderFormData[K],
  ) => {
    setForm({ ...form, [key]: value })
  }

  // 更新某个模型的字段
  const updateModel = (
    index: number,
    field: keyof CustomLLMModelInfo,
    value: string,
  ) => {
    const models = form.models.map((m, i) => {
      if (i !== index) return m
      if (field === 'context_window') {
        return { ...m, context_window: Number(value) || 0 }
      }
      return { ...m, [field]: value }
    })
    setForm({ ...form, models })
  }

  // 删除某个模型行
  const removeModel = (index: number) => {
    setForm({
      ...form,
      models: form.models.filter((_, i) => i !== index),
    })
  }

  // 添加一个空模型行
  const addModel = () => {
    setForm({
      ...form,
      models: [...form.models, { model_id: '', context_window: 0 }],
    })
  }

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 1100,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        backgroundColor: 'rgba(0, 0, 0, 0.5)',
      }}
      onClick={onCancel}
    >
      <div
        style={{
          width: '560px',
          maxWidth: '90vw',
          maxHeight: '85vh',
          overflow: 'auto',
          backgroundColor: 'var(--bg-secondary)',
          border: '1px solid var(--border)',
          borderRadius: 'var(--radius-md)',
          padding: '20px',
          boxShadow: 'var(--shadow-lg)',
        }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* 标题 */}
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            marginBottom: '16px',
          }}
        >
          <span
            style={{
              fontSize: '14px',
              fontWeight: 600,
              color: 'var(--text-primary)',
            }}
          >
            {editing ? '编辑供应商' : '添加供应商'}
          </span>
          <button
            onClick={onCancel}
            style={{
              border: 'none',
              background: 'transparent',
              color: 'var(--text-tertiary)',
              cursor: 'pointer',
              fontSize: '16px',
            }}
          >
            ✕
          </button>
        </div>

        {/* 表单字段 */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
          {/* 名称 */}
          <div>
            <label style={fieldLabelStyle}>名称 *</label>
            <TextInput
              value={form.name}
              onChange={(v) => updateField('name', v)}
              placeholder="如：我的 OpenAI 代理"
            />
          </div>

          {/* Base URL */}
          <div>
            <label style={fieldLabelStyle}>Base URL *</label>
            <TextInput
              value={form.base_url}
              onChange={(v) => updateField('base_url', v)}
              placeholder="https://api.openai.com/v1"
            />
          </div>

          {/* API Key（带显示/隐藏按钮） */}
          <div>
            <label style={fieldLabelStyle}>API Key</label>
            <div style={{ display: 'flex', gap: '4px' }}>
              <TextInput
                type={showKey ? 'text' : 'password'}
                value={form.api_key}
                onChange={(v) => updateField('api_key', v)}
                placeholder="sk-..."
                style={{ flex: 1 }}
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
          </div>

          {/* API 格式 */}
          <div>
            <label style={fieldLabelStyle}>API 格式</label>
            <Select
              value={form.api_format}
              onChange={(v) => updateField('api_format', v as ApiFormat)}
              options={[
                { value: 'openai', label: 'OpenAI' },
                { value: 'anthropic', label: 'Anthropic' },
              ]}
            />
            <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '4px', fontFamily: 'var(--font-mono)' }}>
              {form.api_format === 'openai'
                ? 'Base URL 填到 /v1，如 https://api.openai.com/v1（SDK 自动补 /chat/completions）'
                : 'Base URL 填根域名，如 https://api.anthropic.com（代码自动补 /v1/messages）'}
            </div>
          </div>

          {/* 模型列表编辑器 */}
          <div>
            <div
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                marginBottom: '8px',
              }}
            >
              <label style={fieldLabelStyle}>模型列表</label>
              <button
                onClick={addModel}
                style={btnStyle('default')}
              >
                + 添加模型
              </button>
            </div>

            {form.models.length === 0 ? (
              <div
                style={{
                  padding: '16px',
                  textAlign: 'center',
                  color: 'var(--text-tertiary)',
                  fontSize: '12px',
                  border: '1px dashed var(--border)',
                  borderRadius: 'var(--radius-sm)',
                }}
              >
                还没有添加模型，点击"添加模型"开始
              </div>
            ) : (
              <div
                style={{
                  display: 'flex',
                  flexDirection: 'column',
                  gap: '6px',
                }}
              >
                {/* 表头 */}
                <div
                  style={{
                    display: 'grid',
                    gridTemplateColumns: '1fr 120px 32px',
                    gap: '6px',
                    fontSize: '11px',
                    color: 'var(--text-tertiary)',
                    padding: '0 2px',
                  }}
                >
                  <span>模型 ID</span>
                  <span>上下文窗口</span>
                  <span />
                </div>
                {form.models.map((m, i) => (
                  <div
                    key={i}
                    style={{
                      display: 'grid',
                      gridTemplateColumns: '1fr 120px 32px',
                      gap: '6px',
                      alignItems: 'center',
                    }}
                  >
                    <TextInput
                      value={m.model_id}
                      onChange={(v) => updateModel(i, 'model_id', v)}
                      placeholder="gpt-4o"
                    />
                    <input
                      type="number"
                      value={m.context_window || ''}
                      onChange={(e) =>
                        updateModel(i, 'context_window', e.target.value)
                      }
                      placeholder="128000"
                      style={numberInputStyle}
                    />
                    <button
                      onClick={() => removeModel(i)}
                      title="删除此模型"
                      style={{
                        border: 'none',
                        background: 'transparent',
                        color: 'var(--error)',
                        cursor: 'pointer',
                        fontSize: '14px',
                        padding: '4px',
                        borderRadius: 'var(--radius-sm)',
                      }}
                    >
                      ✕
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* 错误提示 */}
        <StatusMessage type="error" message={error} />

        {/* 操作按钮 */}
        <div
          style={{
            display: 'flex',
            justifyContent: 'flex-end',
            gap: '8px',
            marginTop: '16px',
          }}
        >
          <button onClick={onCancel} style={btnStyle('default')}>
            取消
          </button>
          <button
            onClick={onSave}
            disabled={saving}
            style={{
              ...btnStyle('primary'),
              opacity: saving ? 0.5 : 1,
              cursor: saving ? 'not-allowed' : 'pointer',
            }}
          >
            {saving ? '保存中...' : '保存'}
          </button>
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// 样式工具函数
// ---------------------------------------------------------------------------

// 按钮基础样式
function btnStyle(
  variant: 'primary' | 'default' | 'danger',
): React.CSSProperties {
  const base: React.CSSProperties = {
    padding: '6px 14px',
    fontSize: '13px',
    fontFamily: 'var(--font-ui)',
    borderRadius: 'var(--radius-sm)',
    cursor: 'pointer',
    border: '1px solid var(--border)',
    flexShrink: 0,
  }
  if (variant === 'primary') {
    return {
      ...base,
      backgroundColor: 'var(--accent)',
      color: '#1a1a1a',
      border: 'none',
      fontWeight: 600,
    }
  }
  if (variant === 'danger') {
    return {
      ...base,
      backgroundColor: 'transparent',
      color: 'var(--error)',
      borderColor: 'rgba(255, 107, 107, 0.3)',
    }
  }
  return {
    ...base,
    backgroundColor: 'var(--bg-primary)',
    color: 'var(--text-secondary)',
  }
}

// 标签样式
function tagStyle(bg: string, color: string): React.CSSProperties {
  return {
    backgroundColor: bg,
    color,
    fontSize: '10px',
    padding: '1px 6px',
    borderRadius: 'var(--radius-sm)',
    fontWeight: 500,
    flexShrink: 0,
  }
}

// 表单字段标签样式
const fieldLabelStyle: React.CSSProperties = {
  display: 'block',
  color: 'var(--text-secondary)',
  fontSize: '12px',
  marginBottom: '4px',
  fontFamily: 'var(--font-ui)',
  fontWeight: 500,
}

// 数字输入框样式（复用原子组件的输入风格）
const numberInputStyle: React.CSSProperties = {
  width: '100%',
  boxSizing: 'border-box',
  backgroundColor: 'var(--bg-primary)',
  border: '1px solid var(--border)',
  color: 'var(--text-primary)',
  padding: '6px 10px',
  fontSize: '13px',
  outline: 'none',
  borderRadius: 'var(--radius-sm)',
  fontFamily: 'var(--font-ui)',
}

export default LLMSettingsSection
