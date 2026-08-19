import { useState } from 'react'
import type { TokenUsage } from '../../../stores/useChatStore'
import { useGitStatus, dedupeChanges } from '../useGitStatus'

interface SummaryCardProps {
  // 当前会话工作块数量
  blockCount: number
  // token 用量（计算上下文占比）
  usage: TokenUsage
  // 点击产物文件项：在文件上下文内打开对应文件（可选）
  onOpenFile?: (path: string) => void
}

// 上下文窗口大小
const WINDOW_SIZE = 200000

// 分区标题：可折叠 chevron，独立展开/收起各段
function SectionHeader({ title, collapsed, onToggle }: { title: string; collapsed: boolean; onToggle: () => void }) {
  return (
    <button
      onClick={onToggle}
      title={collapsed ? `展开${title}` : `收起${title}`}
      aria-expanded={!collapsed}
      style={{
        width: '100%',
        display: 'flex',
        alignItems: 'center',
        gap: '6px',
        margin: '10px 0 4px',
        padding: '0 12px',
        border: 'none',
        background: 'transparent',
        cursor: 'pointer',
        fontSize: '11px',
        color: 'var(--text-tertiary)',
        letterSpacing: '0.5px',
        fontFamily: 'var(--font-ui)',
        textAlign: 'left',
      }}
    >
      <span style={{ flex: 1 }}>{title}</span>
      <svg
        width="12"
        height="12"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        style={{ transition: 'transform var(--transition-fast)', transform: collapsed ? 'rotate(-90deg)' : 'rotate(0deg)' }}
      >
        <path d="M6 9l6 6 6-6" />
      </svg>
    </button>
  )
}

// 空数据占位文案
function Empty({ text }: { text: string }) {
  return (
    <div style={{ fontSize: '12px', color: 'var(--text-tertiary)', padding: '2px 12px' }}>
      {text}
    </div>
  )
}

// 概要卡：进展（会话工作块）/ 产物（git 变更文件）/ 引用（上下文用量）
function SummaryCard({ blockCount, usage, onOpenFile }: SummaryCardProps) {
  const gitStatus = useGitStatus()
  // 产物只列文件：git 会把新增目录也报为变更项，过滤掉
  const artifacts = gitStatus
    ? dedupeChanges(gitStatus.changes).filter((c) => !c.path.endsWith('/'))
    : []

  // 上下文占比：当前 prompt tokens / 窗口大小，超过 80% 变红
  const current = usage.last_prompt_tokens
  const percent = Math.min(100, (current / WINDOW_SIZE) * 100)

  // 三段独立折叠状态（默认全部展开）
  const [collapsed, setCollapsed] = useState({ progress: false, artifacts: false, refs: false })
  const toggle = (k: 'progress' | 'artifacts' | 'refs') =>
    setCollapsed((prev) => ({ ...prev, [k]: !prev[k] }))

  return (
    <div style={{ paddingBottom: '10px' }}>
      {/* 进展 */}
      <SectionHeader title="进展" collapsed={collapsed.progress} onToggle={() => toggle('progress')} />
      {!collapsed.progress &&
        (blockCount > 0 ? (
          <div style={{ fontSize: '12px', color: 'var(--text-primary)', padding: '2px 12px' }}>
            已完成 {blockCount} 个工作块
          </div>
        ) : (
          <Empty text="暂无进展" />
        ))}

      {/* 产物：文件项可点击在文件上下文内打开 */}
      <SectionHeader title="产物" collapsed={collapsed.artifacts} onToggle={() => toggle('artifacts')} />
      {!collapsed.artifacts &&
        (artifacts.length === 0 ? (
          <Empty text="暂无产物" />
        ) : (
          artifacts.map((c) => (
            <button
              key={c.path}
              onClick={() => onOpenFile?.(c.path)}
              title={c.path}
              style={{
                display: 'block',
                width: '100%',
                fontWeight: 400,
                fontSize: '12px',
                color: 'var(--text-secondary)',
                textAlign: 'left',
                padding: '1px 12px',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
                fontFamily: 'var(--font-mono)',
                border: 'none',
                background: 'transparent',
                cursor: onOpenFile ? 'pointer' : 'default',
                transition: 'all var(--transition-fast)',
              }}
              onMouseEnter={(e) => {
                if (onOpenFile) {
                  e.currentTarget.style.background = 'var(--bg-tertiary)'
                  e.currentTarget.style.color = 'var(--text-primary)'
                }
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.background = 'transparent'
                e.currentTarget.style.color = 'var(--text-secondary)'
              }}
            >
              {c.path}
            </button>
          ))
        ))}

      {/* 引用：上下文用量 */}
      <SectionHeader title="引用" collapsed={collapsed.refs} onToggle={() => toggle('refs')} />
      {!collapsed.refs && (
        <div style={{ padding: '4px 12px 0' }}>
          <div
            style={{
              height: '4px',
              backgroundColor: 'var(--bg-tertiary)',
              borderRadius: '2px',
              overflow: 'hidden',
            }}
          >
            <div
              style={{
                height: '100%',
                width: `${percent}%`,
                backgroundColor: percent > 80 ? 'var(--error)' : 'var(--border-strong)',
                transition: 'width 0.3s',
              }}
            />
          </div>
          <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '3px' }}>
            上下文 {current.toLocaleString()} / {WINDOW_SIZE.toLocaleString()}
          </div>
        </div>
      )}
    </div>
  )
}

export default SummaryCard
