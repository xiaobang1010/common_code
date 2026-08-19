import ChatStream from './ai/ChatStream'
import ChatInput from './ai/ChatInput'
import { useChatStore } from '../stores/useChatStore'

interface AIPanelProps {
  // 是否已打开工作区：控制对话流空态（无工作区时显示引导）
  hasWorkspace: boolean
  onOpenWorkspace: () => void
  // 当前运行任务所属会话（并发约束提示用），null 表示无任务运行
  currentTaskSessionId: string | null
}

// AI 面板：对话流 + 输入区。
// 原顶部 44px 工具条（工作区/分支选择、业务图标、状态点、新建任务）已上提
// 合并进自绘标题栏（TitleBar），此处不再保留
function AIPanel({ hasWorkspace, onOpenWorkspace, currentTaskSessionId }: AIPanelProps) {
  // 局部订阅：流式更新只影响当前工作块，本面板只在关联状态变化时重渲
  const isStreaming = useChatStore(s => s.isStreaming)
  const sendMessage = useChatStore(s => s.sendMessage)
  const abort = useChatStore(s => s.abort)
  const permissionRequest = useChatStore(s => s.permissionRequest)
  const resolvePermission = useChatStore(s => s.resolvePermission)
  const questionRequest = useChatStore(s => s.questionRequest)
  const answerQuestion = useChatStore(s => s.answerQuestion)
  const permissionMode = useChatStore(s => s.permissionMode)
  const setPermissionMode = useChatStore(s => s.setPermissionMode)

  return (
    <div
      style={{
        backgroundColor: 'var(--bg-secondary)',
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        overflow: 'hidden',
        position: 'relative',
        // 微妙的顶部光晕，让 AI 面板有"主角感"
        boxShadow: isStreaming
          ? 'inset 0 1px 0 rgba(255, 255, 255, 0.06)'
          : 'inset 0 1px 0 rgba(255, 255, 255, 0.02)',
        transition: 'box-shadow 400ms ease',
      }}
    >
      {/* 对话流 */}
      <ChatStream hasWorkspace={hasWorkspace} onOpenWorkspace={onOpenWorkspace} />

      {/* 底部输入区：外层仅纵向间距，横向留白由限宽列承担（与消息列同一宽度基准对齐） */}
      <div
        style={{
          padding: '12px 0 14px',
          borderTop: '1px solid var(--border)',
          flexShrink: 0,
          background: 'linear-gradient(180deg, transparent, rgba(0, 0, 0, 0.15))',
        }}
      >
        {/* 限宽列：与 ChatStream 消息列共用 --content-max-width 与 --content-pad-x，
            保证输入框与消息内容同宽、左缘对齐（宽窄屏均成立） */}
        <div
          style={{
            maxWidth: 'var(--content-max-width)',
            width: '100%',
            margin: '0 auto',
            boxSizing: 'border-box',
            padding: '0 var(--content-pad-x)',
          }}
        >
        <ChatInput
          onSend={sendMessage}
          isStreaming={isStreaming}
          onStop={abort}
          permissionRequest={permissionRequest}
          onResolve={resolvePermission}
          questionRequest={questionRequest}
          onAnswer={answerQuestion}
          permissionMode={permissionMode}
          onPermissionModeChange={setPermissionMode}
          currentTaskSessionId={currentTaskSessionId}
        />
        </div>
      </div>
    </div>
  )
}

export default AIPanel
