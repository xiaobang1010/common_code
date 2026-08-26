import { useMemo, useState } from 'react'
import { useGitStatus, dedupeChanges, type GitChange } from '../useGitStatus'
import DiffView from './DiffView'

// 扩展名 → 语言徽标（文字缩写 + 主题色），近似常见语言图标的观感
const LANG_BADGES: Record<string, { label: string; color: string }> = {
  ts: { label: 'TS', color: '#3178c6' },
  tsx: { label: 'TSX', color: '#61dafb' },
  js: { label: 'JS', color: '#f7df1e' },
  jsx: { label: 'JSX', color: '#61dafb' },
  py: { label: 'PY', color: '#4B8BBE' },
  json: { label: '{}', color: '#cbcb41' },
  md: { label: 'MD', color: '#519aba' },
  css: { label: 'CSS', color: '#a071c9' },
  html: { label: 'H5', color: '#e34c26' },
}

// 文件行左侧的语言徽标：小圆角块 + 缩写文字，未识别的扩展名走中性色
function LangBadge({ path }: { path: string }) {
  const dot = path.lastIndexOf('.')
  const ext = dot >= 0 ? path.slice(dot + 1).toLowerCase() : ''
  const badge = LANG_BADGES[ext]
  if (!badge) {
    return (
      <span
        style={{
          width: '20px',
          height: '18px',
          borderRadius: '4px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          fontSize: '9px',
          fontWeight: 700,
          color: 'var(--text-tertiary)',
          border: '1px solid var(--border)',
          flexShrink: 0,
        }}
      >
        ·
      </span>
    )
  }
  return (
    <span
      style={{
        width: '20px',
        height: '18px',
        borderRadius: '4px',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        fontSize: '8px',
        fontWeight: 700,
        color: badge.color,
        background: `${badge.color}22`,
        flexShrink: 0,
      }}
    >
      {badge.label}
    </span>
  )
}

// 展开箭头：展开时旋转 90 度
function Chevron({ expanded }: { expanded: boolean }) {
  return (
    <svg
      width="12"
      height="12"
      viewBox="0 0 24 24"
      fill="none"
      stroke="var(--text-tertiary)"
      strokeWidth="2.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      style={{
        flexShrink: 0,
        transform: expanded ? 'rotate(0deg)' : 'rotate(-90deg)',
        transition: 'transform 0.15s ease',
      }}
    >
      <path d="M6 9l6 6 6-6" />
    </svg>
  )
}

// 扁平文件行：徽标 + 文件名 + 灰色目录路径 + 增删行数 + 展开箭头。
// 点击在行下方行内展开差异（展开状态由父组件按路径管理，一次只展开一个）。
function FileRow({
  change,
  expanded,
  sideBySide,
  onToggle,
}: {
  change: GitChange
  expanded: boolean
  sideBySide: boolean
  onToggle: () => void
}) {
  const [hovered, setHovered] = useState(false)
  const name = change.path.split('/').pop() || change.path
  const dir = change.path.includes('/') ? change.path.slice(0, change.path.lastIndexOf('/') + 1) : ''

  return (
    <div>
      <div
        onClick={onToggle}
        data-testid="review-file-row"
        title={change.path}
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '8px',
          paddingLeft: '10px',
          paddingRight: '10px',
          height: '30px',
          cursor: 'pointer',
          fontSize: '12px',
          backgroundColor: hovered ? 'var(--bg-tertiary)' : 'transparent',
          transition: 'background var(--transition-fast)',
        }}
      >
        <LangBadge path={change.path} />
        {/* 文件名不缩写，目录路径允许省略号截断 */}
        <span
          style={{
            color: 'var(--text-primary)',
            fontWeight: 500,
            whiteSpace: 'nowrap',
            flexShrink: 0,
          }}
        >
          {name}
        </span>
        <span
          style={{
            color: 'var(--text-tertiary)',
            fontSize: '11px',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
            flex: 1,
            textAlign: 'left',
          }}
        >
          {dir}
        </span>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: '11px', color: 'var(--success)', flexShrink: 0 }}>
          +{change.additions}
        </span>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: '11px', color: 'var(--error)', flexShrink: 0 }}>
          -{change.deletions}
        </span>
        <Chevron expanded={expanded} />
      </div>

      {/* 行内展开的差异区：一次只挂载当前展开的文件 */}
      {expanded && (
        <div data-testid="review-inline-diff-wrap">
          <DiffView path={change.path} sideBySide={sideBySide} />
        </div>
      )}
    </div>
  )
}

// 布局切换按钮（单栏/双栏）统一样式
function modeButtonStyle(active: boolean): React.CSSProperties {
  return {
    background: active ? 'var(--bg-tertiary)' : 'transparent',
    border: 'none',
    color: active ? 'var(--text-primary)' : 'var(--text-secondary)',
    cursor: 'pointer',
    fontSize: '11px',
    padding: '3px 10px',
    borderRadius: 'var(--radius-sm)',
  }
}

// 审查卡：按改动文件直接平铺，点击行内展开前后对比。
// 顶部工具栏：左侧单栏/双栏切换（全局作用于展开的差异，默认单栏），右侧手动刷新。
function ReviewCard() {
  const { data: gitStatus, refresh } = useGitStatus()
  // 当前展开的文件路径（null = 全部收起；一次只展开一个）
  const [expandedPath, setExpandedPath] = useState<string | null>(null)
  // 对比布局：默认单栏
  const [sideBySide, setSideBySide] = useState(false)

  const changes = useMemo(() => {
    if (!gitStatus) return []
    // 只保留文件变更：git 会把新增目录（路径以 / 结尾）也报为变更项
    return dedupeChanges(gitStatus.changes).filter((c) => !c.path.endsWith('/'))
  }, [gitStatus])

  return (
    <div data-testid="review-list" style={{ display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0 }}>
      {/* 工具栏：布局切换 + 手动刷新 */}
      <div
        data-testid="review-toolbar"
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '6px 10px',
          flexShrink: 0,
        }}
      >
        <div style={{ display: 'flex', gap: '2px' }} role="group" aria-label="对比布局切换">
          <button
            onClick={() => setSideBySide(false)}
            data-testid="diff-mode-inline"
            style={modeButtonStyle(!sideBySide)}
          >
            单栏
          </button>
          <button
            onClick={() => setSideBySide(true)}
            data-testid="diff-mode-side"
            style={modeButtonStyle(sideBySide)}
          >
            双栏
          </button>
        </div>
        <button
          onClick={refresh}
          data-testid="review-refresh"
          title="重新拉取变更清单"
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '4px',
            background: 'transparent',
            border: 'none',
            color: 'var(--text-secondary)',
            cursor: 'pointer',
            fontSize: '12px',
            padding: '3px 8px',
            borderRadius: 'var(--radius-sm)',
          }}
          onMouseEnter={(e) => (e.currentTarget.style.color = 'var(--text-primary)')}
          onMouseLeave={(e) => (e.currentTarget.style.color = 'var(--text-secondary)')}
        >
          <svg
            width="12"
            height="12"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <polyline points="23 4 23 10 17 10" />
            <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10" />
          </svg>
          刷新
        </button>
      </div>

      {/* 变更文件列表 */}
      <div style={{ flex: 1, minHeight: 0, overflowY: 'auto', padding: '4px 0' }}>
        {changes.length === 0 ? (
          <div style={{ fontSize: '12px', color: 'var(--text-tertiary)', padding: '4px 12px' }}>
            暂无待审查变更
          </div>
        ) : (
          changes.map((change) => (
            <FileRow
              key={change.path}
              change={change}
              expanded={expandedPath === change.path}
              sideBySide={sideBySide}
              onToggle={() => setExpandedPath((prev) => (prev === change.path ? null : change.path))}
            />
          ))
        )}
      </div>
    </div>
  )
}

export default ReviewCard
