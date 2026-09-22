import type { ReactNode } from 'react'

// 工具标签标识（概要/搜索/审查）：开关状态由 App 层持有，供标题栏开关与入口卡片共用。
// 不在表内两例：终端是会话区底部的独立面板（入口在标题栏终端开关）；「文件」不是标签——
// 面板在没有工具激活时的基础视图就是文件视图，打开文件也会自动切过去
export type ToolId = 'summary' | 'search' | 'review'

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

// 搜索图标：文档 + 放大镜
const SearchIcon = (
  <svg {...iconProps}>
    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
    <path d="M14 2v6h6" />
    <circle cx="10.5" cy="14" r="2.2" />
    <path d="M12.2 15.7l1.8 1.8" />
  </svg>
)

// 审查图标：剪贴板 + 对勾（审核语义，避免与搜索的放大镜混淆）
const ReviewIcon = (
  <svg {...iconProps}>
    <path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2" />
    <rect x="8" y="2" width="8" height="4" rx="1" />
    <path d="M9 13l2 2 4-4" />
  </svg>
)

// 工具标签元信息：顺序即标签栏的展示顺序（默认标签集 + 按需打开）。
// 不能随意从表中删除条目：标签渲染、面板映射与 localStorage 恢复均依赖此表
export const TOOL_META: { id: ToolId; title: string; icon: ReactNode }[] = [
  { id: 'summary', title: '概要', icon: SummaryIcon },
  { id: 'search', title: '搜索', icon: SearchIcon },
  { id: 'review', title: '审查', icon: ReviewIcon },
]
