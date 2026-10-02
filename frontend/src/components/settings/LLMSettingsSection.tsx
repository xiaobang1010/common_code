// LLM 模型设置区 - 自定义供应商列表管理（增删改查 + 测试连接 + 激活）
// 接 /api/llm-providers 系列接口，支持多供应商多模型的完整管理
// 保存/删除/激活后通过 store 广播，触发 fetchState 刷新 model 显示

import { useEffect, useState, useCallback } from 'react'
import { llmApi } from '../../api/client'
import type {
  CustomLLMProviderInfo,
  CustomLLMModelInfo,
  ModelInputType,
  ApiFormat,
} from '../../api/client'
import { useSettingsStore } from '../../stores/useSettingsStore'
import { TextInput, Select, StatusMessage } from '../ui'

// 输入类型展示标签（存储用英文枚举值）
const INPUT_TYPE_LABELS: Array<[ModelInputType, string]> = [
  ['text', '文本'],
  ['image', '图片'],
  ['video', '视频'],
  ['pdf', 'PDF'],
]

// 映射快捷填充：中性行为描述文案，内容为两种合法形态的示例
const EFFORT_TEMPLATE = '{"reasoning_effort": "{reasoningLevel}"}'
const THINKING_TEMPLATE =
  '{"disabled": {"thinking": {"type": "disabled"}}, "low": {"thinking": {"type": "enabled"}, "reasoning_effort": "low"}, "max": {"thinking": {"type": "enabled"}, "reasoning_effort": "max"}}'
const THINKING_TEMPLATE_LEVELS = ['disabled', 'low', 'max']

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
    // 本地预检（服务端仍是最终裁判）：等级非空不重复、映射为合法非空 JSON 对象
    for (const m of form.models.filter((x) => x.model_id.trim())) {
      const levels = m.reasoning_levels ?? []
      if (levels.some((l) => !l.trim())) {
        setError(`模型 ${m.model_id}：推理等级存在空值`)
        return
      }
      if (new Set(levels).size !== levels.length) {
        setError(`模型 ${m.model_id}：推理等级存在重复值`)
        return
      }
      const raw = (m.reasoning_params_map ?? '').trim()
      if (raw) {
        try {
          const obj = JSON.parse(raw)
          if (
            typeof obj !== 'object' || obj === null || Array.isArray(obj) ||
            Object.keys(obj).length === 0
          ) {
            setError(`模型 ${m.model_id}：推理参数映射必须是非空 JSON 对象`)
            return
          }
        } catch {
          setError(`模型 ${m.model_id}：推理参数映射不是合法 JSON`)
          return
        }
      }
    }
    setSaving(true)
    setError('')
    try {
      // 组装提交数据（models 为全量替换语义，恒携带全部字段），过滤空行
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
            max_output_tokens: Number(m.max_output_tokens) || 32768,
            input_types: m.input_types?.length ? m.input_types : (['text'] as ModelInputType[]),
            reasoning_levels: m.reasoning_levels ?? [],
            reasoning_params_map: (m.reasoning_params_map ?? '').trim(),
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
      // 刷新本地列表 + store，再广播触发 fetchState 更新
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
                    ? 'var(--success-soft)'
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
                        : 'var(--text-primary)',
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
              ? 'var(--success-soft)'
              : 'var(--error-soft)',
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
  // 高级配置展开状态：模型行下标 -> 是否展开
  const [expandedRows, setExpandedRows] = useState<Record<number, boolean>>({})

  // 更新表单中某个字段
  const updateField = <K extends keyof ProviderFormData>(
    key: K,
    value: ProviderFormData[K],
  ) => {
    setForm({ ...form, [key]: value })
  }

  // 按补丁更新某个模型条目（基础行与高级面板共用）
  const patchModel = (index: number, patch: Partial<CustomLLMModelInfo>) => {
    setForm({
      ...form,
      models: form.models.map((m, i) => (i === index ? { ...m, ...patch } : m)),
    })
  }

  // 更新某个模型的标量字段
  const updateModel = (
    index: number,
    field: 'model_id' | 'context_window',
    value: string,
  ) => {
    if (field === 'context_window') {
      patchModel(index, { context_window: Number(value) || 0 })
      return
    }
    patchModel(index, { model_id: value })
  }

  // 删除某个模型行
  const removeModel = (index: number) => {
    setForm({
      ...form,
      models: form.models.filter((_, i) => i !== index),
    })
  }

  // 添加一个空模型行（新字段带默认值，保存时恒全量提交）
  const addModel = () => {
    setForm({
      ...form,
      models: [
        ...form.models,
        {
          model_id: '',
          context_window: 0,
          max_output_tokens: 32768,
          input_types: ['text'],
          reasoning_levels: [],
          reasoning_params_map: '',
        },
      ],
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
        backgroundColor: 'var(--scrim)',
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
                    gridTemplateColumns: '1fr 120px 64px 32px',
                    gap: '6px',
                    fontSize: '11px',
                    color: 'var(--text-tertiary)',
                    padding: '0 2px',
                  }}
                >
                  <span>模型 ID</span>
                  <span>上下文窗口</span>
                  <span />
                  <span />
                </div>
                {form.models.map((m, i) => (
                  <div key={i}>
                    <div
                      style={{
                        display: 'grid',
                        gridTemplateColumns: '1fr 120px 64px 32px',
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
                        onClick={() =>
                          setExpandedRows((prev) => ({ ...prev, [i]: !prev[i] }))
                        }
                        style={{
                          ...btnStyle('default'),
                          padding: '4px 6px',
                          fontSize: '11px',
                        }}
                      >
                        {expandedRows[i] ? '▾ 高级' : '▸ 高级'}
                      </button>
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
                    {expandedRows[i] && (
                      <ModelAdvancedPanel model={m} onPatch={(p) => patchModel(i, p)} />
                    )}
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
// ModelAdvancedPanel - 模型高级配置（输出上限/输入类型/推理等级/参数映射）
// ---------------------------------------------------------------------------

interface ModelAdvancedPanelProps {
  model: CustomLLMModelInfo
  onPatch: (patch: Partial<CustomLLMModelInfo>) => void
}

function ModelAdvancedPanel({ model, onPatch }: ModelAdvancedPanelProps) {
  // 等级编辑区的草稿输入与「+」展开态
  const [levelDraft, setLevelDraft] = useState('')
  const [addingLevel, setAddingLevel] = useState(false)
  const levels = model.reasoning_levels ?? []
  const inputTypes = model.input_types?.length ? model.input_types : (['text'] as ModelInputType[])

  const toggleInputType = (t: ModelInputType, checked: boolean) => {
    // text 恒选：取消勾选无效，服务端也会补正
    if (t === 'text') return
    const next = checked
      ? [...inputTypes.filter((x) => x !== t), t]
      : inputTypes.filter((x) => x !== t)
    onPatch({ input_types: next })
  }

  // 新增等级：草稿非空且不重复才收录，收录后收起输入框
  const commitLevel = () => {
    const v = levelDraft.trim()
    setAddingLevel(false)
    setLevelDraft('')
    if (!v || levels.includes(v)) return
    onPatch({ reasoning_levels: [...levels, v] })
  }

  const removeLevel = (idx: number) => {
    onPatch({ reasoning_levels: levels.filter((_, i) => i !== idx) })
  }

  return (
    <div
      style={{
        margin: '6px 0 10px',
        padding: '10px 12px',
        backgroundColor: 'var(--bg-primary)',
        border: '1px solid var(--border)',
        borderRadius: 'var(--radius-sm)',
        display: 'flex',
        flexDirection: 'column',
        gap: '10px',
      }}
    >
      {/* 最大输出 Token */}
      <div>
        <label style={fieldLabelStyle}>最大输出 Token</label>
        <input
          type="number"
          value={model.max_output_tokens ?? ''}
          onChange={(e) =>
            onPatch({ max_output_tokens: Number(e.target.value) || 0 })
          }
          placeholder="32768"
          style={numberInputStyle}
        />
      </div>

      {/* 输入类型勾选组 */}
      <div>
        <label style={fieldLabelStyle}>输入类型</label>
        <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap' }}>
          {INPUT_TYPE_LABELS.map(([t, label]) => (
            <label
              key={t}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '4px',
                fontSize: '12px',
                color: 'var(--text-secondary)',
                cursor: t === 'text' ? 'not-allowed' : 'pointer',
              }}
            >
              <input
                type="checkbox"
                checked={inputTypes.includes(t)}
                disabled={t === 'text'}
                onChange={(e) => toggleInputType(t, e.target.checked)}
              />
              {label}
            </label>
          ))}
        </div>
      </div>

      {/* 推理等级编辑区：等级胶囊按添加顺序从低到高，悬停出删除叉，「+」就地输入新增 */}
      <div>
        <label style={fieldLabelStyle}>推理等级（从低到高）</label>
        <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap', alignItems: 'center' }}>
          {levels.map((lv, i) => (
            <LevelChip key={`${lv}-${i}`} label={lv} onRemove={() => removeLevel(i)} />
          ))}
          {addingLevel ? (
            <input
              autoFocus
              type="text"
              value={levelDraft}
              onChange={(e) => setLevelDraft(e.target.value)}
              onBlur={commitLevel}
              onKeyDown={(e) => {
                if (e.key === 'Enter') commitLevel()
                if (e.key === 'Escape') { setAddingLevel(false); setLevelDraft('') }
              }}
              placeholder="等级名"
              style={{
                width: '96px',
                padding: '3px 8px',
                fontSize: '12px',
                fontFamily: 'var(--font-mono)',
                backgroundColor: 'var(--bg-secondary)',
                border: '1px solid var(--border-strong)',
                borderRadius: '999px',
                color: 'var(--text-primary)',
                outline: 'none',
              }}
            />
          ) : (
            <button
              onClick={() => setAddingLevel(true)}
              title="添加等级"
              style={{
                width: '26px',
                height: '24px',
                padding: 0,
                border: '1px solid var(--border)',
                borderRadius: 'var(--radius-sm)',
                background: 'var(--bg-tertiary)',
                color: 'var(--text-secondary)',
                fontSize: '14px',
                lineHeight: '22px',
                cursor: 'pointer',
              }}
            >
              +
            </button>
          )}
        </div>
      </div>

      {/* 推理参数映射 */}
      <div>
        <label style={fieldLabelStyle}>推理参数映射</label>
        <textarea
          value={model.reasoning_params_map ?? ''}
          onChange={(e) => onPatch({ reasoning_params_map: e.target.value })}
          placeholder='{"reasoning_effort": "{reasoningLevel}"}'
          rows={3}
          style={{
            ...numberInputStyle,
            resize: 'vertical',
            fontFamily: 'var(--font-mono)',
            fontSize: '12px',
          }}
        />
        <div style={{ display: 'flex', gap: '6px', marginTop: '6px' }}>
          <button
            onClick={() => onPatch({ reasoning_params_map: EFFORT_TEMPLATE })}
            style={btnStyle('default')}
          >
            reasoning_effort 透传模板
          </button>
          <button
            onClick={() =>
              onPatch({
                reasoning_params_map: THINKING_TEMPLATE,
                // 等级为空时一并填入该模板配套的等级集，避免形态不匹配
                ...(levels.length === 0
                  ? { reasoning_levels: THINKING_TEMPLATE_LEVELS }
                  : {}),
              })
            }
            style={btnStyle('default')}
          >
            thinking 开关按等级模板
          </button>
        </div>
        <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '4px', lineHeight: 1.5 }}>
          选中等级后，映射求值结果会合并进请求体（值为 null 的键表示删除）。
          模板形态用 {'{reasoningLevel}'} 占位符；或按等级各写一组参数（顶层键与等级一致）。
        </div>
      </div>
    </div>
  )
}

// 等级胶囊：常态只显示文本，悬停浮现删除叉（避免静态界面堆满操作按钮）
function LevelChip({ label, onRemove }: { label: string; onRemove: () => void }) {
  const [hover, setHover] = useState(false)
  return (
    <span
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: '4px',
        padding: '3px 10px',
        backgroundColor: 'var(--bg-tertiary)',
        border: '1px solid var(--border)',
        borderRadius: 'var(--radius-sm)',
        fontSize: '12px',
        color: 'var(--text-primary)',
        fontFamily: 'var(--font-mono)',
      }}
    >
      {label}
      {hover && (
        <button
          onClick={onRemove}
          title="删除等级"
          style={{
            border: 'none',
            background: 'transparent',
            color: 'var(--text-tertiary)',
            cursor: 'pointer',
            fontSize: '11px',
            padding: 0,
            lineHeight: 1,
          }}
        >
          ✕
        </button>
      )}
    </span>
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
      backgroundColor: 'var(--button-primary-bg)',
      color: 'var(--button-primary-text)',
      border: 'none',
      fontWeight: 600,
    }
  }
  if (variant === 'danger') {
    return {
      ...base,
      backgroundColor: 'transparent',
      color: 'var(--error)',
      borderColor: 'var(--error)',
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
