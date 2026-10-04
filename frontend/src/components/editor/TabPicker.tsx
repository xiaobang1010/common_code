import type { ReactNode } from 'react'
import type { ToolId } from './toolMeta'

// 产物区初始选择页：无文件标签且无工具标签激活时呈现，
// 版式对齐参考设计——居中标题 + 副标题 + 单行卡片网格（窄面板自动换行）。
// 卡片为三项：终端 / 审查 / 浏览器（搜索、智能体不在卡片项内，走各自既有入口）

// 卡片点击路由决策（纯函数，供单测）：卡片 id → 应触发的动作
export type PickerAction =
  | { kind: 'terminal' }
  | { kind: 'tool'; toolId: Extract<ToolId, 'review' | 'browser'> }

export function pickerActionFor(cardId: string): PickerAction | null {
  if (cardId === 'terminal') return { kind: 'terminal' }
  if (cardId === 'review' || cardId === 'browser') return { kind: 'tool', toolId: cardId }
  return null
}

// 图标统一样式参数（与工具标签图标同规格）
const iconProps = {
  width: 18,
  height: 18,
  viewBox: '0 0 24 24',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.7,
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
}

// 终端图标：提示符窗口
const TerminalIcon = (
  <svg {...iconProps}>
    <rect x="3" y="4" width="18" height="16" rx="2" />
    <path d="M7 9l3 3-3 3M12.5 15H17" />
  </svg>
)

// 审查图标：剪贴板 + 对勾（与工具标签审查图标同路径语义）
const ReviewIcon = (
  <svg {...iconProps}>
    <path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2" />
    <rect x="8" y="2" width="8" height="4" rx="1" />
    <path d="M9 13l2 2 4-4" />
  </svg>
)

// 浏览器图标：地球
const BrowserIcon = (
  <svg {...iconProps}>
    <circle cx="12" cy="12" r="9" />
    <path d="M3 12h18" />
    <path d="M12 3a14 14 0 0 1 0 18a14 14 0 0 1 0-18" />
  </svg>
)

const CARDS: { id: string; title: string; icon: ReactNode }[] = [
  { id: 'terminal', title: '终端', icon: TerminalIcon },
  { id: 'review', title: '审查', icon: ReviewIcon },
  { id: 'browser', title: '浏览器', icon: BrowserIcon },
]

interface TabPickerProps {
  onAction: (action: PickerAction) => void
}

export default function TabPicker({ onAction }: TabPickerProps) {
  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        gap: '6px',
        userSelect: 'none',
        padding: '16px 12px',
      }}
    >
      <span
        style={{
          fontSize: '14px',
          fontWeight: 600,
          color: 'var(--text-primary)',
          fontFamily: 'var(--font-ui)',
        }}
      >
        打开标签页
      </span>
      <span
        style={{
          fontSize: '12px',
          color: 'var(--text-tertiary)',
          fontFamily: 'var(--font-ui)',
          marginBottom: '10px',
        }}
      >
        选择要在侧边面板中打开的标签。
      </span>
      <div
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          justifyContent: 'center',
          gap: '10px',
          maxWidth: '360px',
        }}
      >
        {CARDS.map((card) => (
          <button
            key={card.id}
            onClick={() => {
              const action = pickerActionFor(card.id)
              if (action) onAction(action)
            }}
            style={{
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              gap: '8px',
              width: '96px',
              padding: '16px 8px 14px',
              border: '1px solid var(--border-subtle)',
              borderRadius: 'var(--radius-md)',
              backgroundColor: 'var(--bg-secondary)',
              color: 'var(--text-secondary)',
              cursor: 'pointer',
              fontFamily: 'var(--font-ui)',
              fontSize: '12px',
              transition: 'all var(--transition-fast)',
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.backgroundColor = 'var(--bg-tertiary)'
              e.currentTarget.style.color = 'var(--text-primary)'
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.backgroundColor = 'var(--bg-secondary)'
              e.currentTarget.style.color = 'var(--text-secondary)'
            }}
          >
            {card.icon}
            <span>{card.title}</span>
          </button>
        ))}
      </div>
    </div>
  )
}
