import { useState } from 'react'
import { useGitStatus, dedupeChanges } from '../useGitStatus'
import { useSpecProgress, deriveProgress } from '../../../hooks/useSpecProgress'

interface SummaryCardProps {
  // 当前会话 id：spec 进展按会话归属拉取（null = 无活跃会话，仅显示工作块数）
  sessionId: string | null
  // 当前会话工作块数量
  blockCount: number
  // 点击产物文件项：在文件上下文内打开对应文件（可选）
  onOpenFile?: (path: string) => void
}

// 上下文用量展示已移至输入区底部（ChatInput），概要卡不再重复

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

// 进展行：标签 + 完成度数字；验收全勾时数字绿色强调，与胶囊卡一致
function ProgressRow({ label, done, total, passed }: { label: string; done: number; total: number; passed: boolean }) {
  return (
    <div style={{ fontSize: '12px', color: 'var(--text-primary)', padding: '2px 12px' }}>
      {label}{' '}
      <span style={{ color: passed ? 'var(--success)' : undefined }}>
        {done}/{total}
      </span>
    </div>
  )
}

// 概要卡：进展（spec 进度 + 会话工作块）/ 产物（git 变更文件汇总 + 文件列表）
function SummaryCard({ sessionId, blockCount, onOpenFile }: SummaryCardProps) {
  const { data: gitStatus } = useGitStatus()
  // spec 进展按会话归属拉取，与胶囊卡共用同一数据源（useSpecProgress）与口径（deriveProgress）
  const { data: specData } = useSpecProgress(sessionId)
  // 产物只列文件：git 会把新增目录也报为变更项，过滤掉
  const artifacts = gitStatus
    ? dedupeChanges(gitStatus.changes).filter((c) => !c.path.endsWith('/'))
    : []

  // 进展口径：优先任务、回退验证（皆空回退工作块数）；验收全勾时数字绿色
  const progress = deriveProgress(specData)
  const checks = specData?.checks
  const checksPassed = !!checks && checks.total > 0 && checks.done === checks.total
  // 变更汇总：与胶囊卡产物区块同用 git.totals，保证两处数字一致
  const totals = gitStatus?.totals ?? { files: 0, additions: 0, deletions: 0 }

  // 两段独立折叠状态（默认全部展开）
  const [collapsed, setCollapsed] = useState({ progress: false, artifacts: false })
  const toggle = (k: 'progress' | 'artifacts') =>
    setCollapsed((prev) => ({ ...prev, [k]: !prev[k] }))

  return (
    <div style={{ paddingBottom: '10px' }}>
      {/* 进展：有 spec 显示任务/验证进度（口径同胶囊卡），其下保留工作块计数 */}
      <SectionHeader title="进展" collapsed={collapsed.progress} onToggle={() => toggle('progress')} />
      {!collapsed.progress && (
        <>
          {specData && specData.tasks.total > 0 && (
            <ProgressRow label="任务" done={specData.tasks.done} total={specData.tasks.total} passed={checksPassed} />
          )}
          {specData && specData.checks.total > 0 && (
            <ProgressRow label="验证" done={specData.checks.done} total={specData.checks.total} passed={checksPassed} />
          )}
          {blockCount > 0 ? (
            <div style={{ fontSize: '12px', color: 'var(--text-primary)', padding: '2px 12px' }}>
              已完成 {blockCount} 个工作块
            </div>
          ) : (
            !progress && <Empty text="暂无进展" />
          )}
        </>
      )}

      {/* 产物：汇总行数字与胶囊卡一致，文件项可点击在文件上下文内打开 */}
      <SectionHeader title="产物" collapsed={collapsed.artifacts} onToggle={() => toggle('artifacts')} />
      {!collapsed.artifacts &&
        (artifacts.length === 0 ? (
          <Empty text="暂无产物" />
        ) : (
          <>
            <div style={{ display: 'flex', gap: '6px', fontSize: '12px', color: 'var(--text-secondary)', padding: '2px 12px' }}>
              <span>{totals.files} 文件</span>
              <span style={{ color: 'var(--success)' }}>+{totals.additions}</span>
              <span style={{ color: 'var(--error)' }}>−{totals.deletions}</span>
            </div>
            {artifacts.map((c) => (
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
            ))}
          </>
        ))}
    </div>
  )
}

export default SummaryCard
