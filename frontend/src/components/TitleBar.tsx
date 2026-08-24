import { useState } from 'react'
import { useChatStore } from '../stores/useChatStore'
import BrainStatusIcon from './BrainStatusIcon'

interface TitleBarProps {
  workspaceSelector: React.ReactNode
  branchSelector: React.ReactNode
  // 编辑区展开状态：面板开关展开编辑区并聚焦最近工具标签
  panelActive: boolean
  onTogglePanel: () => void
  onOpenSettings: () => void
  onNewSession: () => void
  // 当前任务标题（侧栏折叠时仍可见）
  currentTaskTitle: string
  // 当前查看会话是否有后台任务在跑：切回运行中会话时无前台流式连接，
  // 呼吸灯仍要按「运行中」闪烁
  taskRunning?: boolean
}

// WebKit 私有属性（CSSProperties 未收录），用类型断言封装
const appRegion = (region: 'drag' | 'no-drag'): React.CSSProperties =>
  ({ WebkitAppRegion: region }) as React.CSSProperties

// 平台判定：Windows 右侧需要给系统 overlay 窗口按钮留空位；macOS 左侧避让交通灯
const IS_WINDOWS = navigator.userAgent.includes('Windows')
const IS_MAC = navigator.userAgent.includes('Mac')

// Windows overlay 窗口按钮占位宽度（最小化/最大化/关闭三键）
const OVERLAY_RESERVED = 138
// macOS 交通灯按钮占位宽度
const TRAFFIC_LIGHTS_RESERVED = 78

// 设置入口：齿轮按钮，点击直接打开设置面板（原溢出菜单的重载/DevTools/设置
// 已精简，只保留设置）
function SettingsButton({ onOpenSettings }: { onOpenSettings: () => void }) {
  const [hovered, setHovered] = useState(false)

  return (
    <button
      onClick={onOpenSettings}
      title="设置"
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        width: '28px',
        height: '28px',
        border: `1px solid ${hovered ? 'var(--border-strong)' : 'var(--border)'}`,
        borderRadius: 'var(--radius-sm)',
        background: hovered ? 'var(--hover-bg)' : 'transparent',
        color: hovered ? 'var(--text-primary)' : 'var(--text-secondary)',
        cursor: 'pointer',
        transition: 'all var(--transition-fast)',
        flexShrink: 0,
      }}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
        <circle cx="12" cy="12" r="3" />
        <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33h.01a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51h.01a1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82v.01a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
      </svg>
    </button>
  )
}

// 自绘标题栏：整行是可拖拽区（drag），交互控件标 no-drag
// 左侧 logo + 状态点 + 工作区/分支选择；右侧业务图标 + 溢出菜单
// Windows 右侧留 overlay 空位（原生窗口按钮，前端不重复绘制）；
// macOS 左侧避让交通灯按钮
function TitleBar({
  workspaceSelector,
  branchSelector,
  panelActive,
  onTogglePanel,
  onOpenSettings,
  onNewSession,
  currentTaskTitle,
  taskRunning = false,
}: TitleBarProps) {
  const isStreaming = useChatStore(s => s.isStreaming)
  // 运行中判定：前台流式连接，或当前查看会话有后台任务（轮询展示进展）
  const active = isStreaming || taskRunning

  return (
    <div
      style={{
        height: '38px',
        display: 'flex',
        alignItems: 'center',
        gap: '8px',
        paddingLeft: IS_MAC ? `${TRAFFIC_LIGHTS_RESERVED}px` : '12px',
        paddingRight: IS_WINDOWS ? `${OVERLAY_RESERVED}px` : '12px',
        borderBottom: '1px solid var(--border)',
        backgroundColor: 'var(--bg-base)',
        flexShrink: 0,
        // 整行可拖拽窗口
        ...appRegion('drag'),
        userSelect: 'none',
      }}
    >
      {/* 左侧：状态点 + 记忆图标 + 工作区/分支选择 */}
      <span
        title={active ? '思考中' : '就绪'}
        style={{
          width: '8px',
          height: '8px',
          borderRadius: '50%',
          backgroundColor: active ? 'var(--info)' : 'var(--success)',
          boxShadow: active
            ? 'var(--info-glow)'
            : 'var(--success-glow)',
          animation: active ? 'breathe 1.4s ease-in-out infinite' : 'none',
          flexShrink: 0,
        }}
      />
      {/* 记忆状态图标：暗色=未启用，蓝色脉冲=加载中，蓝色常亮=就绪 */}
      <BrainStatusIcon />
      <div style={{ display: 'flex', alignItems: 'center', gap: '6px', minWidth: 0, ...appRegion('no-drag') }}>
        {workspaceSelector}
        {branchSelector}
        {/* 当前任务名：中间弹性区，侧栏折叠时仍可见，长标题省略号截断 */}
        {currentTaskTitle && (
          <span
            title={currentTaskTitle}
            style={{
              flex: 1,
              minWidth: 0,
              marginLeft: '8px',
              paddingLeft: '8px',
              borderLeft: '1px solid var(--border)',
              fontSize: '12px',
              fontFamily: 'var(--font-ui)',
              fontWeight: 500,
              color: 'var(--text-primary)',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            {currentTaskTitle}
          </span>
        )}
      </div>

      {/* 右侧：业务图标 + 溢出菜单（no-drag，点击不被拖拽吞掉） */}
      <div
        style={{
          marginLeft: 'auto',
          display: 'flex',
          alignItems: 'center',
          gap: '8px',
          flexShrink: 0,
          ...appRegion('no-drag'),
        }}
      >
        {/* 面板开关：展开编辑区并聚焦最近工具标签 */}
        <button
          onClick={onTogglePanel}
          title={panelActive ? '隐藏面板' : '打开面板'}
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            width: '28px',
            height: '28px',
            border: '1px solid',
            borderColor: panelActive ? 'var(--border-strong)' : 'var(--border)',
            borderRadius: 'var(--radius-sm)',
            background: panelActive ? 'var(--selected-bg)' : 'transparent',
            color: panelActive ? 'var(--text-primary)' : 'var(--text-secondary)',
            cursor: 'pointer',
            transition: 'all var(--transition-fast)',
            flexShrink: 0,
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.borderColor = 'var(--border-strong)'
            e.currentTarget.style.color = 'var(--text-primary)'
          }}
          onMouseLeave={(e) => {
            if (!panelActive) {
              e.currentTarget.style.borderColor = 'var(--border)'
              e.currentTarget.style.color = 'var(--text-secondary)'
            }
          }}
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <rect x="3" y="4" width="18" height="16" rx="2" />
            <path d="M15 4v16" />
          </svg>
        </button>

        {/* 新建任务 */}
        <button
          onClick={onNewSession}
          title="新建任务"
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            width: '28px',
            height: '28px',
            border: '1px solid var(--border)',
            borderRadius: 'var(--radius-sm)',
            background: 'transparent',
            color: 'var(--text-secondary)',
            cursor: 'pointer',
            transition: 'all var(--transition-fast)',
            flexShrink: 0,
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.borderColor = 'var(--border-strong)'
            e.currentTarget.style.color = 'var(--text-primary)'
            e.currentTarget.style.backgroundColor = 'var(--hover-bg)'
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.borderColor = 'var(--border)'
            e.currentTarget.style.color = 'var(--text-secondary)'
            e.currentTarget.style.backgroundColor = 'transparent'
          }}
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M12 5v14M5 12h14" />
          </svg>
        </button>

        {/* 设置入口（齿轮，直接打开设置面板） */}
        <SettingsButton onOpenSettings={onOpenSettings} />
      </div>
    </div>
  )
}

export default TitleBar
