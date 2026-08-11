import { useCallback, useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import SummaryCard from './cards/SummaryCard'
import TerminalCard from './cards/TerminalCard'
import FilesCard from './cards/FilesCard'
import ReviewCard from './cards/ReviewCard'
import SearchPanel from '../sidebar/SearchPanel'
import Resizer from '../Resizer'
import { useChatStore } from '../../stores/useChatStore'

// 五张卡片的标识（App 层也需要持有选中卡状态，故导出）
export type CardId = 'summary' | 'terminal' | 'files' | 'search' | 'review'

// 面板形态：列表态（窄栏入口）/ 聚焦态（宽面板铺开内容）
export type PanelMode = 'list' | 'focus'

interface InspectorPanelProps {
  // 点击文件在编辑器打开
  onFileOpen: (path: string) => void
  // 当前工作区路径，null 表示未选择工作区
  workspacePath: string | null
  // 面板形态与选中卡由 App 层持有：面板隐藏再打开时能恢复
  mode: PanelMode
  activeCard: CardId
  onEnterFocus: (id: CardId) => void
  onBackToList: () => void
  onCardChange: (id: CardId) => void
}

// 列表态宽度与聚焦态宽度范围
const LIST_WIDTH = 200
const FOCUS_MIN = 360
const FOCUS_MAX_RATIO = 0.75

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

// 文件图标：文件夹
const FilesIcon = (
  <svg {...iconProps}>
    <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z" />
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

// 卡片元信息：顺序即展示顺序
const CARDS: { id: CardId; title: string; icon: ReactNode }[] = [
  { id: 'summary', title: '概要', icon: SummaryIcon },
  { id: 'terminal', title: '终端', icon: TerminalIcon },
  { id: 'files', title: '文件', icon: FilesIcon },
  { id: 'search', title: '搜索', icon: SearchIcon },
  { id: 'review', title: '审查', icon: ReviewIcon },
]

// 列表态的卡片入口行
function CardEntry({ title, icon, onOpen }: { title: string; icon: ReactNode; onOpen: () => void }) {
  const [hovered, setHovered] = useState(false)
  return (
    <button
      onClick={onOpen}
      title={`打开${title}`}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: '10px',
        width: 'calc(100% - 16px)',
        margin: '2px 8px',
        padding: '9px 12px',
        border: '1px solid',
        borderColor: hovered ? 'var(--accent)' : 'var(--border-subtle)',
        borderRadius: 'var(--radius-md)',
        backgroundColor: hovered ? 'var(--accent-soft)' : 'var(--bg-secondary)',
        color: hovered ? 'var(--accent)' : 'var(--text-secondary)',
        fontSize: '13px',
        fontFamily: 'var(--font-ui)',
        fontWeight: 500,
        cursor: 'pointer',
        transition: 'all var(--transition-fast)',
      }}
    >
      <span style={{ display: 'flex', alignItems: 'center', flexShrink: 0 }}>{icon}</span>
      <span style={{ flex: 1, textAlign: 'left' }}>{title}</span>
      {/* 右箭头：提示点击后进入聚焦态 */}
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0 }}>
        <path d="M9 6l6 6-6 6" />
      </svg>
    </button>
  )
}

// 检查器面板：双形态
// - 列表态：200px 窄栏，五行卡片入口，点击任一卡片进入聚焦态
// - 聚焦态：宽面板（默认 45% 窗口宽，可拖拽），顶部标签页切换卡片，内容铺满
// 卡片内容一旦挂载就保持挂载（切卡/回列表用 CSS 隐藏），终端会话不丢失
function InspectorPanel({ onFileOpen, workspacePath, mode, activeCard, onEnterFocus, onBackToList, onCardChange }: InspectorPanelProps) {
  // 局部订阅：工作块数量与 token 用量变化时才重渲
  const blockCount = useChatStore(s => s.blockIds.length)
  const tokenUsage = useChatStore(s => s.tokenUsage)
  // 聚焦态宽度，0 表示首次进入时用默认值
  const [focusWidth, setFocusWidth] = useState(0)

  // 进入聚焦态时首次初始化宽度（默认窗口 45%）
  useEffect(() => {
    if (mode === 'focus' && focusWidth === 0) {
      setFocusWidth(Math.floor(window.innerWidth * 0.45))
    }
  }, [mode, focusWidth])

  // 聚焦态拖拽调宽：分隔条是面板左边界，向右拖（delta 正）变窄，向左拖变宽
  const handleFocusResize = useCallback((delta: number) => {
    setFocusWidth((prev) => {
      const maxW = window.innerWidth * FOCUS_MAX_RATIO
      return Math.max(FOCUS_MIN, Math.min(maxW, prev - delta))
    })
  }, [])

  // 各卡片内容（全部保持挂载，非选中用 display:none 隐藏）
  const cardContents: Record<CardId, ReactNode> = {
    summary: <SummaryCard blockCount={blockCount} usage={tokenUsage} />,
    terminal: <TerminalCard />,
    files: <FilesCard workspacePath={workspacePath} onFileOpen={onFileOpen} />,
    search: <SearchPanel onFileOpen={onFileOpen} />,
    review: <ReviewCard onFileOpen={onFileOpen} />,
  }

  // 内容区：聚焦态可见；列表态整体隐藏但不卸载（终端会话保活）
  const contentLayer = (
    <div style={{ flex: 1, minHeight: 0, display: mode === 'focus' ? 'flex' : 'none', flexDirection: 'column' }}>
      {CARDS.map(({ id }) => (
        <div
          key={id}
          style={{
            flex: 1,
            minHeight: 0,
            overflow: 'auto',
            display: activeCard === id ? 'flex' : 'none',
            flexDirection: 'column',
          }}
        >
          {cardContents[id]}
        </div>
      ))}
    </div>
  )

  // 列表态：窄栏五行入口
  if (mode === 'list') {
    return (
      <div
        style={{
          width: LIST_WIDTH,
          flexShrink: 0,
          height: '100%',
          display: 'flex',
          flexDirection: 'column',
          backgroundColor: 'var(--bg-base)',
          borderLeft: '1px solid var(--border)',
          overflow: 'hidden',
          paddingTop: '8px',
        }}
      >
        {CARDS.map(({ id, title, icon }) => (
          <CardEntry key={id} title={title} icon={icon} onOpen={() => onEnterFocus(id)} />
        ))}
        {/* 内容层隐藏挂载，保活终端 */}
        {contentLayer}
      </div>
    )
  }

  // 聚焦态：左侧分隔条 + 宽面板（标签栏 + 内容区）
  return (
    <>
      <Resizer direction="horizontal" onResize={handleFocusResize} />
      <div
        style={{
          width: focusWidth,
          flexShrink: 0,
          minWidth: FOCUS_MIN,
          height: '100%',
          display: 'flex',
          flexDirection: 'column',
          backgroundColor: 'var(--bg-base)',
          borderLeft: '1px solid var(--border)',
          overflow: 'hidden',
        }}
      >
        {/* 顶部标签栏：返回按钮 + 五张卡标签 */}
        <div
          style={{
            display: 'flex',
            alignItems: 'stretch',
            height: '38px',
            flexShrink: 0,
            borderBottom: '1px solid var(--border)',
            backgroundColor: 'var(--bg-primary)',
          }}
        >
          {/* 返回列表态 */}
          <button
            onClick={onBackToList}
            title="收起为列表"
            style={{
              border: 'none',
              background: 'transparent',
              color: 'var(--text-tertiary)',
              cursor: 'pointer',
              padding: '0 10px',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              transition: 'all var(--transition-fast)',
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.background = 'var(--bg-tertiary)'
              e.currentTarget.style.color = 'var(--accent)'
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.background = 'transparent'
              e.currentTarget.style.color = 'var(--text-tertiary)'
            }}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
              <path d="M15 6l-6 6 6 6" />
            </svg>
          </button>
          {/* 卡片标签页 */}
          {CARDS.map(({ id, title, icon }) => {
            const active = activeCard === id
            return (
              <button
                key={id}
                onClick={() => onCardChange(id)}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '6px',
                  padding: '0 14px',
                  border: 'none',
                  borderBottom: active ? '2px solid var(--accent)' : '2px solid transparent',
                  background: active ? 'var(--bg-base)' : 'transparent',
                  color: active ? 'var(--text-primary)' : 'var(--text-tertiary)',
                  fontSize: '12px',
                  fontFamily: 'var(--font-ui)',
                  fontWeight: active ? 500 : 400,
                  cursor: 'pointer',
                  transition: 'all var(--transition-fast)',
                }}
                onMouseEnter={(e) => {
                  if (!active) {
                    e.currentTarget.style.color = 'var(--text-secondary)'
                    e.currentTarget.style.background = 'var(--bg-tertiary)'
                  }
                }}
                onMouseLeave={(e) => {
                  if (!active) {
                    e.currentTarget.style.color = 'var(--text-tertiary)'
                    e.currentTarget.style.background = 'transparent'
                  }
                }}
              >
                <span style={{ display: 'flex', alignItems: 'center' }}>{icon}</span>
                {title}
              </button>
            )
          })}
        </div>

        {/* 内容区 */}
        {contentLayer}
      </div>
    </>
  )
}

export default InspectorPanel
