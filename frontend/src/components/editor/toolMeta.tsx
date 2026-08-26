import type { ReactNode } from 'react'

// 工具标签标识（概要/终端/文件/搜索/审查）：开关状态由 App 层持有，供标题栏开关与入口卡片共用
export type ToolId = 'summary' | 'terminal' | 'files' | 'search' | 'review'

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

// 文件图标：文件夹（与文件树/文件标签语义一致）
const FilesIcon = (
  <svg {...iconProps}>
    <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z" />
    <path d="M3 12h18" />
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

// 工具标签元信息：顺序即标签栏与入口卡片的展示顺序（默认三标签 + 按需打开）
// railHidden：入口卡片（IconRail）不渲染该条目，但功能保留（标签栏/面板/Ctrl+K 照常），
// 不能直接从表中删除：标签渲染、面板映射与 localStorage 恢复均依赖此表
export const TOOL_META: { id: ToolId; title: string; icon: ReactNode; railHidden?: boolean }[] = [
  { id: 'summary', title: '概要', icon: SummaryIcon },
  { id: 'terminal', title: '终端', icon: TerminalIcon },
  { id: 'files', title: '文件', icon: FilesIcon },
  { id: 'search', title: '搜索', icon: SearchIcon, railHidden: true },
  { id: 'review', title: '审查', icon: ReviewIcon },
]
