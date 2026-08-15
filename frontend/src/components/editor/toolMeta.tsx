import type { ReactNode } from 'react'

// 工具标签标识（概要/终端/搜索/审查）：开关状态由 App 层持有，供标题栏开关与图标轨共用
export type ToolId = 'summary' | 'terminal' | 'search' | 'review'

// 图标统一样式参数
const iconProps = {
  width: 14,
  height: 14,
  viewBox: '0 0 24 24',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.7,
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
}

// 概要图标：列表文档
const SummaryIcon = (
  <svg {...iconProps}>
    <path d="M8 6h13M8 12h13M8 18h13" />
    <circle cx="3.5" cy="6" r="0.5" fill="currentColor" />
    <circle cx="3.5" cy="12" r="0.5" fill="currentColor" />
    <circle cx="3.5" cy="18" r="0.5" fill="currentColor" />
  </svg>
)

// 终端图标：命令行提示符
const TerminalIcon = (
  <svg {...iconProps}>
    <path d="M4 17l6-5-6-5" />
    <path d="M12 19h8" />
  </svg>
)

// 搜索图标：文档 + 放大镜（与审查卡的纯放大镜区分）
const SearchIcon = (
  <svg {...iconProps}>
    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
    <path d="M14 2v6h6" />
    <circle cx="10.5" cy="14" r="2.2" />
    <path d="M12.2 15.7l1.8 1.8" />
  </svg>
)

// 审查图标：放大镜
const ReviewIcon = (
  <svg {...iconProps}>
    <circle cx="11" cy="11" r="7" />
    <path d="M21 21l-4.3-4.3" />
  </svg>
)

// 工具标签元信息：顺序即标签栏与图标轨的展示顺序
export const TOOL_META: { id: ToolId; title: string; icon: ReactNode }[] = [
  { id: 'summary', title: '概要', icon: SummaryIcon },
  { id: 'terminal', title: '终端', icon: TerminalIcon },
  { id: 'search', title: '搜索', icon: SearchIcon },
  { id: 'review', title: '审查', icon: ReviewIcon },
]
