import { useState, useCallback } from 'react'
import { useChatStore } from '../stores/useChatStore'

interface TitleBarProps {
  workspaceSelector: React.ReactNode
  branchSelector: React.ReactNode
  // 右侧检查器面板开关状态
  inspectorVisible: boolean
  onToggleInspector: () => void
  onOpenSettings: () => void
  onNewSession: () => void
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

// 溢出菜单项：菜单栏隐藏后的低频入口（重载/DevTools/设置）
function OverflowMenu({ onOpenSettings }: { onOpenSettings: () => void }) {
  const [open, setOpen] = useState(false)
  const toggle = useCallback(() => setOpen((prev) => !prev), [])

  const handleAction = useCallback((action: () => void) => {
    setOpen(false)
    action()
  }, [])

  const electronAPI = (window as unknown as { electronAPI?: { reload?: () => void; toggleDevTools?: () => void } }).electronAPI

  return (
    <div style={{ position: 'relative' }}>
      <button
        onClick={toggle}
        title="更多"
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          width: '28px',
          height: '28px',
          border: open ? '1px solid var(--accent)' : '1px solid var(--border)',
          borderRadius: 'var(--radius-sm)',
          background: open ? 'var(--accent-soft)' : 'transparent',
          color: open ? 'var(--accent)' : 'var(--text-secondary)',
          cursor: 'pointer',
          transition: 'all var(--transition-fast)',
          flexShrink: 0,
        }}
        onMouseEnter={(e) => {
          e.currentTarget.style.borderColor = 'var(--accent)'
          e.currentTarget.style.color = 'var(--accent)'
        }}
        onMouseLeave={(e) => {
          if (!open) {
            e.currentTarget.style.borderColor = 'var(--border)'
            e.currentTarget.style.color = 'var(--text-secondary)'
          }
        }}
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
          <circle cx="5" cy="12" r="2" />
          <circle cx="12" cy="12" r="2" />
          <circle cx="19" cy="12" r="2" />
        </svg>
      </button>

      {open && (
        <>
          {/* 点击外部关闭 */}
          <div
            style={{ position: 'fixed', inset: 0, zIndex: 99 }}
            onClick={() => setOpen(false)}
          />
          <div
            style={{
              position: 'absolute',
              top: '36px',
              right: '0',
              zIndex: 100,
              minWidth: '140px',
              padding: '4px',
              backgroundColor: 'var(--bg-elevated)',
              border: '1px solid var(--border-strong)',
              borderRadius: 'var(--radius-md)',
              boxShadow: 'var(--shadow-lg)',
              display: 'flex',
              flexDirection: 'column',
            }}
          >
            {electronAPI?.reload && (
              <button
                onClick={() => handleAction(() => electronAPI.reload?.())}
                style={menuItemStyle}
                onMouseEnter={(e) => (e.currentTarget.style.background = 'var(--bg-tertiary)')}
                onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
              >
                重载
              </button>
            )}
            {electronAPI?.toggleDevTools && (
              <button
                onClick={() => handleAction(() => electronAPI.toggleDevTools?.())}
                style={menuItemStyle}
                onMouseEnter={(e) => (e.currentTarget.style.background = 'var(--bg-tertiary)')}
                onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
              >
                开发者工具
              </button>
            )}
            <button
              onClick={() => handleAction(onOpenSettings)}
              style={menuItemStyle}
              onMouseEnter={(e) => (e.currentTarget.style.background = 'var(--bg-tertiary)')}
              onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
            >
              设置
            </button>
          </div>
        </>
      )}
    </div>
  )
}

const menuItemStyle: React.CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  padding: '7px 12px',
  border: 'none',
  background: 'transparent',
  color: 'var(--text-primary)',
  fontSize: '12px',
  fontFamily: 'var(--font-ui)',
  textAlign: 'left',
  cursor: 'pointer',
  borderRadius: 'var(--radius-sm)',
  transition: 'background var(--transition-fast)',
}

// 自绘标题栏：整行是可拖拽区（drag），交互控件标 no-drag
// 左侧 logo + 状态点 + 工作区/分支选择；右侧业务图标 + 溢出菜单
// Windows 右侧留 overlay 空位（原生窗口按钮，前端不重复绘制）；
// macOS 左侧避让交通灯按钮
function TitleBar({
  workspaceSelector,
  branchSelector,
  inspectorVisible,
  onToggleInspector,
  onOpenSettings,
  onNewSession,
}: TitleBarProps) {
  const isStreaming = useChatStore(s => s.isStreaming)

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
        background: 'linear-gradient(180deg, rgba(245, 166, 35, 0.03), transparent)',
        flexShrink: 0,
        // 整行可拖拽窗口
        ...appRegion('drag'),
        userSelect: 'none',
      }}
    >
      {/* 左侧：状态点 + 工作区/分支选择 */}
      <span
        title={isStreaming ? '思考中' : '就绪'}
        style={{
          width: '8px',
          height: '8px',
          borderRadius: '50%',
          backgroundColor: isStreaming ? 'var(--accent)' : 'var(--success)',
          boxShadow: isStreaming
            ? '0 0 10px var(--accent-glow)'
            : '0 0 6px rgba(78, 201, 176, 0.4)',
          animation: isStreaming ? 'breathe 1.4s ease-in-out infinite' : 'none',
          flexShrink: 0,
        }}
      />
      <div style={{ display: 'flex', alignItems: 'center', gap: '6px', minWidth: 0, ...appRegion('no-drag') }}>
        {workspaceSelector}
        {branchSelector}
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
        {/* 检查器面板开关 */}
        <button
          onClick={onToggleInspector}
          title={inspectorVisible ? '隐藏面板' : '打开面板'}
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            width: '28px',
            height: '28px',
            border: '1px solid',
            borderColor: inspectorVisible ? 'var(--accent)' : 'var(--border)',
            borderRadius: 'var(--radius-sm)',
            background: inspectorVisible ? 'var(--accent-soft)' : 'transparent',
            color: inspectorVisible ? 'var(--accent)' : 'var(--text-secondary)',
            cursor: 'pointer',
            transition: 'all var(--transition-fast)',
            flexShrink: 0,
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.borderColor = 'var(--accent)'
            e.currentTarget.style.color = 'var(--accent)'
          }}
          onMouseLeave={(e) => {
            if (!inspectorVisible) {
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
            e.currentTarget.style.borderColor = 'var(--accent)'
            e.currentTarget.style.color = 'var(--accent)'
            e.currentTarget.style.backgroundColor = 'var(--accent-soft)'
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

        {/* 溢出菜单：重载/DevTools/设置 */}
        <OverflowMenu onOpenSettings={onOpenSettings} />
      </div>
    </div>
  )
}

export default TitleBar
