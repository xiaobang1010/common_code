import { useState } from 'react'
import type { QuestionRequest } from '../../stores/useChatStore'

interface Props {
  // 当前待回答的提问请求，为 null 时不显示卡片
  questionRequest: QuestionRequest
  // 用户提交回答时回调
  onAnswer: (answer: string) => void
}

/**
 * 提问卡片 — 模型调用 AskUserQuestion 时展示。
 * 渲染问题文本 + 候选选项按钮，支持自由输入兜底。
 */
function QuestionCard({ questionRequest, onAnswer }: Props) {
  const [customAnswer, setCustomAnswer] = useState('')

  const submitCustom = () => {
    const trimmed = customAnswer.trim()
    if (!trimmed) return
    onAnswer(trimmed)
  }

  return (
    <div
      style={{
        backgroundColor: 'var(--bg-elevated)',
        border: '1px solid var(--border-strong)',
        borderRadius: 'var(--radius-lg)',
        padding: '14px',
        marginBottom: '8px',
        boxShadow: 'var(--shadow-md)',
      }}
    >
      {/* 标题行：提问图标 + AI 提问 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '10px' }}>
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0 }}>
          <circle cx="12" cy="12" r="10" />
          <path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3" />
          <path d="M12 17h.01" />
        </svg>
        <span
          style={{
            color: 'var(--text-primary)',
            fontSize: '13px',
            fontWeight: 600,
            fontFamily: 'var(--font-ui)',
            letterSpacing: '0.2px',
          }}
        >
          AI 提问
        </span>
        {questionRequest.session_id && (
          <span
            style={{
              color: 'var(--text-secondary)',
              fontSize: '11px',
              fontFamily: 'var(--font-ui)',
              marginLeft: '4px',
            }}
          >
            来自会话 {questionRequest.session_id.slice(0, 8)}
          </span>
        )}
      </div>

      {/* 问题文本 */}
      <div
        style={{
          color: 'var(--text-primary)',
          fontSize: '13px',
          lineHeight: 1.6,
          marginBottom: questionRequest.options.length > 0 ? '10px' : '12px',
          whiteSpace: 'pre-wrap',
        }}
      >
        {questionRequest.question}
      </div>

      {/* 候选选项按钮 */}
      {questionRequest.options.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', marginBottom: '12px' }}>
          {questionRequest.options.map(opt => (
            <button
              key={opt.label}
              onClick={() => onAnswer(opt.label)}
              style={{
                textAlign: 'left',
                padding: '8px 12px',
                border: '1px solid var(--border-strong)',
                borderRadius: 'var(--radius-md)',
                cursor: 'pointer',
                backgroundColor: 'transparent',
                transition: 'all var(--transition-fast)',
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.backgroundColor = 'var(--accent-soft)'
                e.currentTarget.style.borderColor = 'var(--accent)'
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.backgroundColor = 'transparent'
                e.currentTarget.style.borderColor = 'var(--border-strong)'
              }}
            >
              <div style={{ color: 'var(--text-primary)', fontSize: '12px', fontWeight: 600, fontFamily: 'var(--font-ui)' }}>
                {opt.label}
              </div>
              {opt.description && (
                <div style={{ color: 'var(--text-tertiary)', fontSize: '11px', marginTop: '2px', fontFamily: 'var(--font-ui)' }}>
                  {opt.description}
                </div>
              )}
            </button>
          ))}
        </div>
      )}

      {/* 自由输入兜底 */}
      <div style={{ display: 'flex', gap: '8px' }}>
        <input
          value={customAnswer}
          onChange={e => setCustomAnswer(e.target.value)}
          onKeyDown={e => {
            if (e.key === 'Enter') {
              e.preventDefault()
              submitCustom()
            }
          }}
          placeholder="或直接输入回答..."
          style={{
            flex: 1,
            padding: '6px 10px',
            border: '1px solid var(--border-strong)',
            borderRadius: 'var(--radius-md)',
            backgroundColor: 'var(--bg-base)',
            color: 'var(--text-primary)',
            fontSize: '12px',
            fontFamily: 'var(--font-ui)',
            outline: 'none',
          }}
        />
        <button
          onClick={submitCustom}
          disabled={!customAnswer.trim()}
          style={{
            padding: '6px 16px',
            border: 'none',
            borderRadius: 'var(--radius-md)',
            cursor: customAnswer.trim() ? 'pointer' : 'default',
            fontSize: '12px',
            fontFamily: 'var(--font-ui)',
            fontWeight: 600,
            background: customAnswer.trim()
              ? 'linear-gradient(135deg, var(--accent), #ff7a45)'
              : 'var(--bg-tertiary)',
            color: customAnswer.trim() ? '#1a1a1a' : 'var(--text-tertiary)',
            transition: 'all var(--transition-fast)',
          }}
        >
          回答
        </button>
      </div>
    </div>
  )
}

export default QuestionCard
