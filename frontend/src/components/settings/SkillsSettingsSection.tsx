// 技能管理区 — 列表展示 + 搜索 + 新建/导入/刷新/删除
// 接 GET /api/skills、POST /api/skills/{create,import,refresh,delete}
// 技能正文不在这里编辑，只看元数据；新建/导入用 Modal 内的表单

import { useEffect, useState, useCallback } from 'react'
import { skillsApi } from '../../api/client'
import { useSettingsStore } from '../../stores/useSettingsStore'
import { TextInput, StatusMessage } from '../ui'
import type { SkillInfo } from '../../api/client'

// 来源标签的颜色映射（source_label：workspace/personal/plugin/bundled）
const sourceStyle: Record<string, { bg: string; color: string; label: string }> = {
  workspace: { bg: 'rgba(78, 201, 176, 0.12)', color: 'var(--success)', label: '工作区' },
  personal: { bg: 'rgba(255, 255, 255, 0.08)', color: 'var(--text-secondary)', label: '个人' },
  plugin: { bg: 'rgba(108, 182, 255, 0.12)', color: 'var(--info)', label: '插件' },
  bundled: { bg: 'rgba(160, 160, 160, 0.12)', color: 'var(--text-tertiary)', label: '内置' },
}

// 弹窗类型：none / create / import
type ModalType = 'none' | 'create' | 'import'

function SkillsSettingsSection() {
  const { refreshSkills } = useSettingsStore()
  const [skills, setSkills] = useState<SkillInfo[]>([])
  const [loading, setLoading] = useState(true)
  const [keyword, setKeyword] = useState('')
  const [modal, setModal] = useState<ModalType>('none')
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')
  const [actionLoading, setActionLoading] = useState(false)

  // 加载技能列表
  const load = useCallback(async () => {
    try {
      const data = await skillsApi.list()
      setSkills(data.skills)
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载技能失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  // 关键词过滤（名称或描述包含，不区分大小写）
  const filtered = keyword.trim()
    ? skills.filter(
        (s) =>
          s.name.toLowerCase().includes(keyword.toLowerCase()) ||
          s.description.toLowerCase().includes(keyword.toLowerCase()),
      )
    : skills

  // 刷新
  const handleRefresh = async () => {
    setError('')
    setSuccess('')
    setActionLoading(true)
    try {
      await skillsApi.refresh()
      await load()
      await refreshSkills()
      setSuccess('已刷新技能缓存')
      setTimeout(() => setSuccess(''), 3000)
    } catch (e) {
      setError(e instanceof Error ? e.message : '刷新失败')
    } finally {
      setActionLoading(false)
    }
  }

  // 删除（仅个人技能可删）
  const handleDelete = async (skill: SkillInfo) => {
    if (!confirm(`确认删除技能「${skill.name}」？此操作不可撤销。`)) return
    setError('')
    setSuccess('')
    setActionLoading(true)
    try {
      await skillsApi.delete(skill.name)
      await load()
      await refreshSkills()
      setSuccess(`已删除技能：${skill.name}`)
      setTimeout(() => setSuccess(''), 3000)
    } catch (e) {
      setError(e instanceof Error ? e.message : '删除失败')
    } finally {
      setActionLoading(false)
    }
  }

  if (loading) {
    return <div style={{ color: 'var(--text-secondary)', fontSize: '13px' }}>加载中...</div>
  }

  return (
    <div>
      {/* 顶部操作栏：搜索框 + 按钮 */}
      <div style={{ display: 'flex', gap: '8px', marginBottom: '16px', alignItems: 'center' }}>
        <div style={{ flex: 1 }}>
          <TextInput
            value={keyword}
            onChange={setKeyword}
            placeholder="搜索技能名称或描述..."
          />
        </div>
        <button
          onClick={() => setModal('create')}
          style={btnStyle('primary')}
        >
          新建
        </button>
        <button
          onClick={() => setModal('import')}
          style={btnStyle('default')}
        >
          导入
        </button>
        <button
          onClick={handleRefresh}
          disabled={actionLoading}
          style={btnStyle('default')}
        >
          {actionLoading ? '...' : '刷新'}
        </button>
      </div>

      {/* 计数 */}
      <div style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '12px' }}>
        共 {skills.length} 个技能{keyword.trim() && `，过滤后 ${filtered.length} 个`}
      </div>

      {/* 技能列表 */}
      {filtered.length === 0 ? (
        <div style={{ padding: '40px 20px', textAlign: 'center', color: 'var(--text-tertiary)' }}>
          <div style={{ fontSize: '32px', marginBottom: '12px' }}>⚡</div>
          <div style={{ fontSize: '13px' }}>
            {keyword.trim() ? '没有匹配的技能' : '暂无技能'}
          </div>
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
          {filtered.map((s) => {
            const src = sourceStyle[s.source_label] || sourceStyle.bundled
            const canDelete = s.source_label === 'personal'
            // 描述合并：description 为主，when_to_use 有值时追加
            const fullDesc = s.when_to_use
              ? `${s.description} ${s.when_to_use}`
              : s.description
            return (
              <div
                key={s.name}
                style={{
                  padding: '12px 14px',
                  backgroundColor: 'var(--bg-primary)',
                  border: '1px solid var(--border)',
                  borderRadius: 'var(--radius-md)',
                }}
              >
                {/* 标题行：名称 + 来源标签 + 删除按钮 */}
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '8px' }}>
                  <span style={{ color: 'var(--text-primary)', fontWeight: 600, fontSize: '13px', flex: 1 }}>
                    {s.name}
                  </span>
                  <span style={tagStyle(src.bg, src.color)}>{src.label}</span>
                  {canDelete && (
                    <button
                      onClick={() => handleDelete(s)}
                      disabled={actionLoading}
                      title="删除此个人技能"
                      style={{
                        border: 'none',
                        background: 'transparent',
                        color: 'var(--error)',
                        cursor: actionLoading ? 'not-allowed' : 'pointer',
                        fontSize: '12px',
                        padding: '2px 6px',
                        opacity: actionLoading ? 0.5 : 1,
                      }}
                    >
                      删除
                    </button>
                  )}
                </div>

                {/* 字段平铺：描述 / 范围 / 状态 / 文件路径 */}
                <div
                  style={{
                    display: 'grid',
                    gridTemplateColumns: 'auto 1fr',
                    gap: '4px 10px',
                    fontSize: '12px',
                    fontFamily: 'var(--font-ui)',
                    lineHeight: 1.6,
                  }}
                >
                  <span style={{ color: 'var(--text-tertiary)' }}>描述</span>
                  <span style={{ color: 'var(--text-secondary)' }}>{fullDesc}</span>

                  <span style={{ color: 'var(--text-tertiary)' }}>范围</span>
                  <span style={{ color: 'var(--text-secondary)' }}>{src.label}</span>

                  <span style={{ color: 'var(--text-tertiary)' }}>状态</span>
                  <span style={{ color: 'var(--success)' }}>
                    {s.user_invocable ? '已启用' : '未启用'}
                  </span>

                  {s.skill_root && (
                    <>
                      <span style={{ color: 'var(--text-tertiary)' }}>文件路径</span>
                      <span style={{ color: 'var(--text-secondary)', fontFamily: 'var(--font-mono)', wordBreak: 'break-all' }}>
                        {s.skill_root}
                      </span>
                    </>
                  )}
                </div>
              </div>
            )
          })}
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

      {/* 新建 / 导入弹窗 */}
      {modal !== 'none' && (
        <SkillModal
          type={modal}
          onClose={() => setModal('none')}
          onDone={async () => {
            setModal('none')
            await load()
            await refreshSkills()
          }}
        />
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// 按钮基础样式
// ---------------------------------------------------------------------------

function btnStyle(variant: 'primary' | 'default'): React.CSSProperties {
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
    return { ...base, backgroundColor: 'var(--button-primary-bg)', color: 'var(--button-primary-text)', border: 'none' }
  }
  return { ...base, backgroundColor: 'var(--bg-primary)', color: 'var(--text-secondary)' }
}

// 来源标签样式
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

// ---------------------------------------------------------------------------
// SkillModal — 新建 / 导入弹窗
// ---------------------------------------------------------------------------

interface SkillModalProps {
  type: 'create' | 'import'
  onClose: () => void
  onDone: () => Promise<void>
}

function SkillModal({ type, onClose, onDone }: SkillModalProps) {
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [whenToUse, setWhenToUse] = useState('')
  const [allowedTools, setAllowedTools] = useState('')
  const [content, setContent] = useState('')
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)

  // 新建提交
  const handleCreate = async () => {
    setError('')
    if (!name.trim() || !description.trim()) {
      setError('技能名和描述必填')
      return
    }
    setSubmitting(true)
    try {
      const tools = allowedTools.trim()
        ? allowedTools.split(',').map((t) => t.trim()).filter(Boolean)
        : null
      await skillsApi.create({
        name: name.trim(),
        description: description.trim(),
        when_to_use: whenToUse.trim(),
        allowed_tools: tools,
      })
      await onDone()
    } catch (e) {
      setError(e instanceof Error ? e.message : '创建失败')
    } finally {
      setSubmitting(false)
    }
  }

  // 导入提交
  const handleImport = async () => {
    setError('')
    if (!name.trim() || !content.trim()) {
      setError('技能名和 SKILL.md 内容必填')
      return
    }
    setSubmitting(true)
    try {
      await skillsApi.import(name.trim(), content)
      await onDone()
    } catch (e) {
      setError(e instanceof Error ? e.message : '导入失败')
    } finally {
      setSubmitting(false)
    }
  }

  const handleSubmit = type === 'create' ? handleCreate : handleImport

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
      onClick={onClose}
    >
      <div
        style={{
          width: '520px',
          maxWidth: '90vw',
          maxHeight: '80vh',
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
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
          <span style={{ fontSize: '14px', fontWeight: 600, color: 'var(--text-primary)' }}>
            {type === 'create' ? '新建技能' : '导入技能'}
          </span>
          <button
            onClick={onClose}
            style={{ border: 'none', background: 'transparent', color: 'var(--text-tertiary)', cursor: 'pointer', fontSize: '16px' }}
          >
            ✕
          </button>
        </div>

        {type === 'create' ? (
          /* 新建表单 */
          <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
            <div>
              <label style={fieldLabelStyle}>技能名 *</label>
              <TextInput
                value={name}
                onChange={setName}
                placeholder="如：code-review（只含字母数字连字符下划线）"
              />
            </div>
            <div>
              <label style={fieldLabelStyle}>描述 *</label>
              <TextInput
                value={description}
                onChange={setDescription}
                placeholder="一句话说明这个技能做什么"
              />
            </div>
            <div>
              <label style={fieldLabelStyle}>使用场景</label>
              <TextInput
                value={whenToUse}
                onChange={setWhenToUse}
                placeholder="什么时候该用这个技能"
              />
            </div>
            <div>
              <label style={fieldLabelStyle}>工具白名单</label>
              <TextInput
                value={allowedTools}
                onChange={setAllowedTools}
                placeholder="逗号分隔，如：Read, Grep, Glob（留空表示全部工具）"
              />
            </div>
          </div>
        ) : (
          /* 导入表单 */
          <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
            <div>
              <label style={fieldLabelStyle}>技能名 *</label>
              <TextInput
                value={name}
                onChange={setName}
                placeholder="导入后的技能名（只含字母数字连字符下划线）"
              />
            </div>
            <div>
              <label style={fieldLabelStyle}>SKILL.md 全文 *</label>
              <textarea
                value={content}
                onChange={(e) => setContent(e.target.value)}
                placeholder="粘贴 SKILL.md 的完整内容（含 frontmatter）"
                style={{
                  width: '100%',
                  boxSizing: 'border-box',
                  minHeight: '200px',
                  backgroundColor: 'var(--bg-primary)',
                  border: '1px solid var(--border)',
                  color: 'var(--text-primary)',
                  padding: '8px 10px',
                  fontSize: '12px',
                  fontFamily: 'var(--font-mono)',
                  borderRadius: 'var(--radius-sm)',
                  resize: 'vertical',
                  outline: 'none',
                }}
              />
            </div>
          </div>
        )}

        <StatusMessage type="error" message={error} />

        {/* 操作按钮 */}
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px', marginTop: '16px' }}>
          <button onClick={onClose} style={btnStyle('default')}>
            取消
          </button>
          <button
            onClick={handleSubmit}
            disabled={submitting}
            style={{
              ...btnStyle('primary'),
              opacity: submitting ? 0.5 : 1,
              cursor: submitting ? 'not-allowed' : 'pointer',
            }}
          >
            {submitting ? '提交中...' : type === 'create' ? '创建' : '导入'}
          </button>
        </div>
      </div>
    </div>
  )
}

const fieldLabelStyle: React.CSSProperties = {
  display: 'block',
  color: 'var(--text-secondary)',
  fontSize: '12px',
  marginBottom: '4px',
  fontFamily: 'var(--font-ui)',
  fontWeight: 500,
}

export default SkillsSettingsSection
