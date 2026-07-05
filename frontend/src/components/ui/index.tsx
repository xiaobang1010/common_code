// 可复用的设置表单原子组件
// 全部复用 index.css 的 CSS 变量，不引入新设计 token

import type { ReactNode, CSSProperties } from 'react'

// ---------------------------------------------------------------------------
// 通用样式
// ---------------------------------------------------------------------------

const inputBaseStyle: CSSProperties = {
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
  transition: 'border-color var(--transition-fast)',
}

const labelStyle: CSSProperties = {
  display: 'block',
  color: 'var(--text-secondary)',
  fontSize: '12px',
  marginBottom: '4px',
  fontFamily: 'var(--font-ui)',
  fontWeight: 500,
}

// ---------------------------------------------------------------------------
// TextInput — 文本输入框
// ---------------------------------------------------------------------------

interface TextInputProps {
  value: string
  onChange: (value: string) => void
  placeholder?: string
  type?: 'text' | 'password'
  disabled?: boolean
  error?: boolean
  style?: CSSProperties
}

export function TextInput({
  value,
  onChange,
  placeholder,
  type = 'text',
  disabled,
  error,
  style,
}: TextInputProps) {
  return (
    <input
      type={type}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      disabled={disabled}
      style={{
        ...inputBaseStyle,
        borderColor: error ? 'var(--error)' : inputBaseStyle.borderColor,
        opacity: disabled ? 0.5 : 1,
        cursor: disabled ? 'not-allowed' : 'text',
        ...style,
      }}
    />
  )
}

// ---------------------------------------------------------------------------
// Select — 下拉选择
// ---------------------------------------------------------------------------

interface SelectOption {
  value: string
  label: string
}

interface SelectProps {
  value: string
  onChange: (value: string) => void
  options: SelectOption[]
  disabled?: boolean
  style?: CSSProperties
}

export function Select({ value, onChange, options, disabled, style }: SelectProps) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      disabled={disabled}
      style={{
        ...inputBaseStyle,
        cursor: disabled ? 'not-allowed' : 'pointer',
        opacity: disabled ? 0.5 : 1,
        ...style,
      }}
    >
      {options.map((opt) => (
        <option key={opt.value} value={opt.value}>
          {opt.label}
        </option>
      ))}
    </select>
  )
}

// ---------------------------------------------------------------------------
// Toggle — 开关
// ---------------------------------------------------------------------------

interface ToggleProps {
  checked: boolean
  onChange: (checked: boolean) => void
  disabled?: boolean
  loading?: boolean
}

export function Toggle({ checked, onChange, disabled, loading }: ToggleProps) {
  const isDisabled = disabled || loading
  return (
    <button
      onClick={() => !isDisabled && onChange(!checked)}
      disabled={isDisabled}
      style={{
        width: '34px',
        height: '18px',
        borderRadius: '10px',
        border: 'none',
        backgroundColor: checked ? 'var(--accent)' : 'var(--bg-tertiary)',
        position: 'relative',
        cursor: isDisabled ? 'not-allowed' : 'pointer',
        opacity: isDisabled ? 0.5 : 1,
        transition: 'background-color var(--transition-fast)',
        flexShrink: 0,
      }}
    >
      <span
        style={{
          position: 'absolute',
          top: '2px',
          left: checked ? '18px' : '2px',
          width: '14px',
          height: '14px',
          borderRadius: '50%',
          backgroundColor: '#fff',
          transition: 'left var(--transition-fast)',
          boxShadow: 'var(--shadow-sm)',
        }}
      />
    </button>
  )
}

// ---------------------------------------------------------------------------
// SettingRow — 标签 + 描述 + 控件的行布局
// ---------------------------------------------------------------------------

interface SettingRowProps {
  label: string
  description?: string
  children: ReactNode
}

export function SettingRow({ label, description, children }: SettingRowProps) {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'flex-start',
        justifyContent: 'space-between',
        gap: '12px',
        padding: '10px 0',
        borderBottom: '1px solid var(--border-subtle)',
      }}
    >
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={labelStyle}>{label}</div>
        {description && (
          <div
            style={{
              color: 'var(--text-tertiary)',
              fontSize: '11px',
              marginTop: '2px',
              lineHeight: 1.5,
            }}
          >
            {description}
          </div>
        )}
      </div>
      <div style={{ flexShrink: 0, display: 'flex', alignItems: 'center' }}>
        {children}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// SettingSection — 带标题与分隔线的分区容器
// ---------------------------------------------------------------------------

interface SettingSectionProps {
  title: string
  description?: string
  children: ReactNode
}

export function SettingSection({ title, description, children }: SettingSectionProps) {
  return (
    <div style={{ marginBottom: '24px' }}>
      <div
        style={{
          fontSize: '13px',
          color: 'var(--text-primary)',
          fontWeight: 600,
          fontFamily: 'var(--font-ui)',
          marginBottom: '4px',
        }}
      >
        {title}
      </div>
      {description && (
        <div
          style={{
            fontSize: '12px',
            color: 'var(--text-tertiary)',
            marginBottom: '8px',
            lineHeight: 1.5,
          }}
        >
          {description}
        </div>
      )}
      <div>{children}</div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// StatusMessage — 统一的成功/错误提示条
// ---------------------------------------------------------------------------

interface StatusMessageProps {
  type: 'success' | 'error'
  message: string
}

export function StatusMessage({ type, message }: StatusMessageProps) {
  if (!message) return null
  return (
    <div
      style={{
        color: type === 'success' ? 'var(--success)' : 'var(--error)',
        fontSize: '12px',
        padding: '6px 10px',
        backgroundColor:
          type === 'success'
            ? 'rgba(78, 201, 176, 0.08)'
            : 'rgba(255, 107, 107, 0.08)',
        borderRadius: 'var(--radius-sm)',
        marginTop: '8px',
      }}
    >
      {message}
    </div>
  )
}
