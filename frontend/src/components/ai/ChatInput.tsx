import { useState, useRef, useEffect, type KeyboardEvent as ReactKeyboardEvent } from 'react'
import type { PermissionRequest, QuestionRequest } from '../../stores/useChatStore'
import { useChatStore } from '../../stores/useChatStore'
import { llmApi, type PermissionMode, type CustomLLMProviderInfo } from '../../api/client'
import { useSettingsStore } from '../../stores/useSettingsStore'
import QuestionCard from './QuestionCard'
import RichChatInput, { type RichChatInputHandle } from './RichChatInput'

// 文件树右键「添加到对话」的事件名：detail 为工作区相对路径，
// ChatInput 监听后在输入框内插入内联引用 chip
export const CHAT_INSERT_REF_EVENT = 'chat-insert-ref'

interface Props {
  onSend: (prompt: string) => boolean | Promise<boolean>
  // 是否正在流式输出（用于显示停止按钮）
  isStreaming: boolean
  // 停止当前对话
  onStop: () => void
  // 当前待确认的权限请求，为 null 时不显示卡片
  permissionRequest: PermissionRequest | null
  // 用户做出权限决策时回调
  onResolve: (decision: 'allow' | 'deny' | 'always_allow') => void
  // 当前待回答的提问请求（AskUserQuestion），为 null 时不显示卡片
  questionRequest: QuestionRequest | null
  // 用户提交提问回答时回调
  onAnswer: (answer: string) => void
  // 当前权限模式
  permissionMode: PermissionMode
  // 切换权限模式
  onPermissionModeChange: (mode: PermissionMode) => void
  // 当前运行任务所属会话（并发约束提示用），null 表示无任务运行
  currentTaskSessionId: string | null
}

// 「上下文容量」面板的分类中文名与色点颜色。
// 行顺序按 token 数降序动态排，这里只定义展示名与配色
const CONTEXT_CATEGORY_META: Record<string, { label: string; color: string }> = {
  mcp_tools: { label: 'MCP 工具', color: '#5b9dff' },
  system_tools: { label: '系统工具', color: '#4585e6' },
  skills: { label: '技能', color: '#3a6fc4' },
  system_prompt: { label: '系统提示词', color: '#315da6' },
  other: { label: '其他', color: '#2a4d8a' },
  messages: { label: '消息', color: '#233f70' },
}

// 数字按万单位格式化：33000 -> 「3.3万」，1000000 -> 「100万」，不足万保持原值
function formatWan(n: number): string {
  if (n < 10000) return String(n)
  const wan = n / 10000
  return `${wan >= 100 ? Math.round(wan) : Math.round(wan * 10) / 10}万`
}

// 内置斜杠命令列表
const BUILTIN_COMMANDS = [
  { name: '/help', desc: '显示帮助' },
  { name: '/clear', desc: '清空对话' },
  { name: '/compact', desc: '压缩历史' },
  { name: '/config', desc: '查看配置' },
  { name: '/model', desc: '切换模型' },
  { name: '/cost', desc: '查看成本' },
  { name: '/exit', desc: '退出' },
]

function ChatInput({ onSend, isStreaming, onStop, permissionRequest, onResolve, questionRequest, onAnswer, permissionMode, onPermissionModeChange, currentTaskSessionId }: Props) {
  // 输入框序列化文本（chip 已还原为 @路径），供补全过滤与发送按钮状态用
  const [text, setText] = useState('')
  // 用户手动关闭补全后置 true，阻止自动弹出，直到下次输入变化
  const [commandsDismissed, setCommandsDismissed] = useState(false)
  const [selectedIdx, setSelectedIdx] = useState(0)
  const [isFocused, setIsFocused] = useState(false)
  const [commands, setCommands] = useState(BUILTIN_COMMANDS)
  const [showPermMenu, setShowPermMenu] = useState(false)
  const richInputRef = useRef<RichChatInputHandle>(null)
  const permMenuRef = useRef<HTMLDivElement>(null)
  // 当前会话 id：区分"本会话在跑"与"其他会话在跑"
  const currentSessionId = useChatStore(s => s.sessionId)
  // 当前会话任务进行中：前台流式，或当前查看的会话正好是后台运行任务的会话。
  // 两种形态都禁用输入、显示停止按钮（停止作用于当前查看会话）
  const taskActive = isStreaming || (currentTaskSessionId !== null && currentSessionId === currentTaskSessionId)

  // ---- 模型快速切换相关状态 ----
  // modelVersion 来自 store，外部（如设置面板）切换模型时会变化，触发重新拉取供应商列表
  const modelVersion = useSettingsStore(s => s.modelVersion)
  const notifyModelChanged = useSettingsStore(s => s.notifyModelChanged)
  // 上下文用量（输入区底部控件展示），模型展示复用下方切换按钮
  const tokenUsage = useChatStore(s => s.tokenUsage)
  // 供应商列表和当前激活的供应商/模型
  const [providers, setProviders] = useState<CustomLLMProviderInfo[]>([])
  const [activeProvider, setActiveProvider] = useState<string | null>(null)
  const [activeModel, setActiveModel] = useState<string | null>(null)
  const [showModelMenu, setShowModelMenu] = useState(false)
  const [switching, setSwitching] = useState(false)
  const modelMenuRef = useRef<HTMLDivElement>(null)
  // 「上下文容量」面板：悬停进度圈即显示，移开延迟 150ms 收起
  // （延迟是给「按钮 → 面板」之间的 8px 空隙留的过渡，避免抖动）
  const [showContextPanel, setShowContextPanel] = useState(false)
  const contextPanelHideTimer = useRef<number | undefined>(undefined)
  // 上下文分类估算（后端 context_metrics 产出，运行中经 SSE 事件实时刷新）
  const contextBreakdown = useChatStore(s => s.contextBreakdown)

  // 点击下拉外部时关闭权限模式菜单
  useEffect(() => {
    if (!showPermMenu) return
    const handleClick = (e: MouseEvent) => {
      if (permMenuRef.current && !permMenuRef.current.contains(e.target as Node)) {
        setShowPermMenu(false)
      }
    }
    document.addEventListener('click', handleClick)
    return () => document.removeEventListener('click', handleClick)
  }, [showPermMenu])

  // 点击下拉外部时关闭模型选择菜单
  useEffect(() => {
    if (!showModelMenu) return
    const handleClick = (e: MouseEvent) => {
      if (modelMenuRef.current && !modelMenuRef.current.contains(e.target as Node)) {
        setShowModelMenu(false)
      }
    }
    document.addEventListener('click', handleClick)
    return () => document.removeEventListener('click', handleClick)
  }, [showModelMenu])

  // 挂载时和模型变更时拉取供应商列表
  // modelVersion 变化说明外部（设置面板或本组件）切换了模型，需要重新拉取
  useEffect(() => {
    llmApi.listCustomProviders()
      .then(data => {
        setProviders(data.providers)
        setActiveProvider(data.active_provider)
        setActiveModel(data.active_model)
      })
      .catch(() => {
        // 接口不可用时保持空列表
      })
  }, [modelVersion])

  // 挂载时从 /api/skills 拉取可用 skill，合并到命令补全列表
  useEffect(() => {
    fetch('/api/skills')
      .then(r => r.json())
      .then(data => {
        if (data.skills && Array.isArray(data.skills)) {
          const skillCmds = data.skills.map((s: any) => ({
            name: `/${s.name}`,
            desc: s.description || s.when_to_use || '',
          }))
          setCommands([...BUILTIN_COMMANDS, ...skillCmds])
        }
      })
      .catch(() => {
        // 接口不可用时只显示内置命令
      })
  }, [])

  const filteredCommands = commands.filter(c => c.name.startsWith(text))

  // 补全列表是否展示：直接从输入值派生，不用 effect 回写状态。
  // 旧实现用 useEffect 监听 value 并回写 showCommands，且依赖里缺少 filteredCommands，
  // 会在发送清空输入与自动回写之间形成反馈环，触发 Maximum update depth exceeded。
  const showCommands = text.startsWith('/') && filteredCommands.length > 0 && !commandsDismissed

  // 输入变化时重置选中项和手动关闭标记（新的一轮输入视为重新打开补全）
  useEffect(() => {
    setSelectedIdx(0)
    setCommandsDismissed(false)
  }, [text])

  // 文件树「添加到对话」：在输入框光标处（或末尾）插入内联引用 chip
  useEffect(() => {
    const handler = (e: Event) => {
      const path = (e as CustomEvent<string>).detail
      if (typeof path === 'string' && path) richInputRef.current?.insertRef(path)
    }
    window.addEventListener(CHAT_INSERT_REF_EVENT, handler)
    return () => window.removeEventListener(CHAT_INSERT_REF_EVENT, handler)
  }, [])

  // 任务运行中发送被拒收的提示：transient 显示，超时自动消失
  const [rejectHint, setRejectHint] = useState(false)
  const rejectTimerRef = useRef<number | undefined>(undefined)

  const handleSend = () => {
    const trimmed = text.trim()
    if (!trimmed || taskActive) return
    const showHint = () => {
      setRejectHint(true)
      if (rejectTimerRef.current) window.clearTimeout(rejectTimerRef.current)
      rejectTimerRef.current = window.setTimeout(() => setRejectHint(false), 3000)
    }
    // 序列化文本已含内联 [文件名](./路径) 引用，直接发送
    Promise.resolve(onSend(trimmed)).then(sent => {
      if (sent) richInputRef.current?.clear()
      else showHint()
    })
  }

  // 补全导航按键：消费后返回 true，未命中补全态交回输入框自身处理
  const handleKeyDown = (e: ReactKeyboardEvent<HTMLDivElement>): boolean => {
    if (!showCommands) return false
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setSelectedIdx(prev => (prev + 1) % filteredCommands.length)
      return true
    }
    if (e.key === 'ArrowUp') {
      e.preventDefault()
      setSelectedIdx(prev => (prev - 1 + filteredCommands.length) % filteredCommands.length)
      return true
    }
    if (e.key === 'Escape') {
      e.preventDefault()
      setCommandsDismissed(true)
      return true
    }
    if (e.key === 'Enter') {
      e.preventDefault()
      richInputRef.current?.setText(filteredCommands[selectedIdx].name + ' ')
      richInputRef.current?.focus()
      return true
    }
    return false
  }

  // 切换模型：调用激活接口 -> 刷新本地状态 -> 广播变更触发 fetchState 更新
  const handleModelSelect = async (providerId: string, modelId: string) => {
    // 点的就是当前激活项，直接关闭菜单
    if (providerId === activeProvider && modelId === activeModel) {
      setShowModelMenu(false)
      return
    }
    setShowModelMenu(false)
    setSwitching(true)
    try {
      await llmApi.activateProvider(providerId, modelId)
      // 刷新本地状态
      const data = await llmApi.listCustomProviders()
      setProviders(data.providers)
      setActiveProvider(data.active_provider)
      setActiveModel(data.active_model)
      // 广播变更，触发 fetchState 等更新显示
      notifyModelChanged()
    } catch {
      // 切换失败，重新拉取恢复正确状态
      try {
        const data = await llmApi.listCustomProviders()
        setProviders(data.providers)
        setActiveProvider(data.active_provider)
        setActiveModel(data.active_model)
      } catch {
        // 拉取也失败就算了
      }
    } finally {
      setSwitching(false)
    }
  }

  // 计算模型按钮显示文本：供应商名 / 模型名
  const activeProviderInfo = providers.find(p => p.id === activeProvider)
  const modelDisplayText = activeProviderInfo && activeModel
    ? `${activeProviderInfo.name} / ${activeModel}`
    : '未配置模型'

  // 上下文窗口大小：当前激活模型的 context_window，取不到（未配置/接口失败）回退 200000
  const contextWindow = providers
    .find(p => p.id === activeProvider)
    ?.models.find(m => m.model_id === activeModel)?.context_window || 200000
  // 上下文占比：当前 prompt tokens / 窗口大小，超过 80% 进度圈变红
  const contextPercent = Math.min(100, (tokenUsage.last_prompt_tokens / contextWindow) * 100)

  // ---- 「上下文容量」面板数据推导 ----
  // 分类行：去掉 total、按 token 降序，占比为 0 的分类不显示
  const breakdownRows = contextBreakdown
    ? Object.entries(contextBreakdown)
        .filter(([key, v]) => key !== 'total' && v > 0)
        .sort((a, b) => b[1] - a[1])
    : []
  const breakdownTotal = contextBreakdown?.total || 0
  // 平均缓存命中率 = 累计缓存命中 / 累计实际发送的输入总量（协议无关口径）。
  // 分母由后端按协议折算：OpenAI 兼容的 prompt_tokens 已含缓存、Anthropic 的不含，
  // 若前端自行相加会把 OpenAI 兼容的分母算成两倍、命中率显示成真实值的一半
  const cacheHitRate =
    tokenUsage.total_input_tokens > 0
      ? (tokenUsage.cache_read_input_tokens / tokenUsage.total_input_tokens) * 100
      : null

  return (
    <div style={{ position: 'relative' }}>
      {/* 任务运行中发送被拒收的提示条 */}
      {rejectHint && (
        <div
          style={{
            margin: '0 0 8px',
            padding: '6px 12px',
            borderRadius: 'var(--radius-md)',
            border: '1px solid var(--warning)',
            background: 'var(--warning-soft)',
            color: 'var(--text-primary)',
            fontSize: '12px',
            fontFamily: 'var(--font-ui)',
          }}
        >
          当前任务正在运行中，消息未发送——请等待完成或点击「停止」后再发送
        </div>
      )}

      {/* 提问卡片 - 模型主动提问时内嵌在输入框上方 */}
      {questionRequest && (
        <QuestionCard questionRequest={questionRequest} onAnswer={onAnswer} />
      )}

      {/* 权限确认卡片 - 内嵌在输入框上方，不是全屏遮罩 */}
      {permissionRequest && (
        <div
          style={{
            backgroundColor: 'var(--bg-elevated)',
            border: '1px solid var(--border-strong)',
            borderRadius: 'var(--radius-lg)',
            padding: '14px',
            marginBottom: '8px',
            boxShadow: 'var(--shadow-md)',
          }}
        >
          {/* 标题行：警告图标 + 权限确认 */}
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '10px' }}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--warning)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0 }}>
              <path d="M12 2 2 22h20L12 2z" />
              <path d="M12 9v4M12 17h.01" />
            </svg>
            <span
              style={{
                color: 'var(--text-primary)',
                fontSize: '13px',
                fontWeight: 600,
                fontFamily: 'var(--font-ui)',
                letterSpacing: '0.2px',
              }}
            >
              权限确认
            </span>
            {permissionRequest.session_id && (
              <span
                style={{
                  color: 'var(--text-secondary)',
                  fontSize: '11px',
                  fontFamily: 'var(--font-ui)',
                  marginLeft: '4px',
                }}
              >
                来自会话 {permissionRequest.session_id.slice(0, 8)}
              </span>
            )}
          </div>

          {/* 工具名 - 等宽字体，警示琥珀色 */}
          <div
            style={{
              color: 'var(--warning)',
              fontSize: '13px',
              fontFamily: 'var(--font-mono)',
              fontWeight: 500,
              marginBottom: '8px',
            }}
          >
            {permissionRequest.tool_name}
          </div>

          {/* 参数 JSON - 等宽字体深色背景可滚动 */}
          <pre
            style={{
              backgroundColor: 'var(--bg-base)',
              border: '1px solid var(--border-subtle)',
              padding: '10px',
              borderRadius: 'var(--radius-md)',
              fontSize: '11px',
              fontFamily: 'var(--font-mono)',
              color: 'var(--text-primary)',
              overflow: 'auto',
              maxHeight: '120px',
              whiteSpace: 'pre-wrap',
              lineHeight: 1.5,
              marginBottom: '8px',
            }}
          >
            {JSON.stringify(permissionRequest.tool_input, null, 2)}
          </pre>

          {/* 原因 - 次要文字色 */}
          <div
            style={{
              color: 'var(--text-secondary)',
              fontSize: '12px',
              lineHeight: 1.5,
              marginBottom: '12px',
            }}
          >
            {permissionRequest.reason}
          </div>

          {/* 底部按钮组 - 靠右排列 */}
          <div style={{ display: 'flex', gap: '8px', justifyContent: 'flex-end' }}>
            {/* 拒绝 - 描边按钮，hover 红色 */}
            <button
              onClick={() => onResolve('deny')}
              style={{
                padding: '6px 14px',
                border: '1px solid var(--border-strong)',
                borderRadius: 'var(--radius-md)',
                cursor: 'pointer',
                fontSize: '12px',
                fontFamily: 'var(--font-ui)',
                fontWeight: 500,
                backgroundColor: 'transparent',
                color: 'var(--text-secondary)',
                transition: 'all var(--transition-fast)',
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.backgroundColor = 'var(--error-soft)'
                e.currentTarget.style.borderColor = 'var(--error)'
                e.currentTarget.style.color = 'var(--error)'
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.backgroundColor = 'transparent'
                e.currentTarget.style.borderColor = 'var(--border-strong)'
                e.currentTarget.style.color = 'var(--text-secondary)'
              }}
            >
              拒绝
            </button>
            {/* 总是允许 - 描边按钮，hover 中性提亮 */}
            <button
              onClick={() => onResolve('always_allow')}
              style={{
                padding: '6px 14px',
                border: '1px solid var(--border-strong)',
                borderRadius: 'var(--radius-md)',
                cursor: 'pointer',
                fontSize: '12px',
                fontFamily: 'var(--font-ui)',
                fontWeight: 500,
                backgroundColor: 'transparent',
                color: 'var(--text-secondary)',
                transition: 'all var(--transition-fast)',
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.backgroundColor = 'var(--hover-bg)'
                e.currentTarget.style.color = 'var(--text-primary)'
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.backgroundColor = 'transparent'
                e.currentTarget.style.color = 'var(--text-secondary)'
              }}
            >
              总是允许
            </button>
            {/* 允许 - 中性高对比主按钮 */}
            <button
              onClick={() => onResolve('allow')}
              style={{
                padding: '6px 16px',
                border: 'none',
                borderRadius: 'var(--radius-md)',
                cursor: 'pointer',
                fontSize: '12px',
                fontFamily: 'var(--font-ui)',
                fontWeight: 600,
                background: 'var(--button-primary-bg-hover)',
                color: 'var(--button-primary-text)',
                boxShadow: 'var(--shadow-sm)',
                transition: 'all var(--transition-fast)',
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.transform = 'translateY(-1px)'
                e.currentTarget.style.boxShadow = 'var(--shadow-md)'
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.transform = 'translateY(0)'
                e.currentTarget.style.boxShadow = 'var(--shadow-sm)'
              }}
            >
              允许
            </button>
          </div>
        </div>
      )}

      {/* 命令补全下拉列表 */}
      {showCommands && (
        <div
          style={{
            position: 'absolute',
            bottom: '100%',
            left: 0,
            right: 0,
            backgroundColor: 'var(--bg-elevated)',
            border: '1px solid var(--border-strong)',
            borderRadius: 'var(--radius-md)',
            marginBottom: '6px',
            maxHeight: '220px',
            overflowY: 'auto',
            zIndex: 10,
            boxShadow: 'var(--shadow-md)',
            backdropFilter: 'blur(8px)',
          }}
        >
          {filteredCommands.map((cmd, idx) => (
            <div
              key={cmd.name}
              onClick={() => {
                richInputRef.current?.setText(cmd.name + ' ')
                richInputRef.current?.focus()
              }}
              style={{
                padding: '8px 14px',
                cursor: 'pointer',
                backgroundColor: idx === selectedIdx ? 'var(--selected-bg)' : 'transparent',
                color: 'var(--text-primary)',
                fontSize: '13px',
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                transition: 'background var(--transition-fast)',
              }}
            >
              <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-primary)' }}>
                {cmd.name}
              </span>
              <span style={{ color: 'var(--text-tertiary)', fontSize: '11px' }}>
                {cmd.desc}
              </span>
            </div>
          ))}
        </div>
      )}

      {/* 输入框容器 - 聚焦时中性边框抬升 */}
      <div
        style={{
          position: 'relative',
          borderRadius: 'var(--radius-lg)',
          background: 'var(--bg-tertiary)',
          border: `1px solid ${isFocused ? 'var(--border-strong)' : 'var(--border)'}`,
          boxShadow: isFocused
            ? '0 0 0 3px var(--focus-ring)'
            : 'none',
          transition: 'all var(--transition)',
        }}
      >
        {/* 富文本输入：文件引用以 chip 内联在文字流中，随文本一起序列化发送 */}
        <RichChatInput
          ref={richInputRef}
          disabled={taskActive}
          onTextChange={setText}
          onSubmit={handleSend}
          onKeyDown={handleKeyDown}
          onFocus={() => setIsFocused(true)}
          onBlur={() => setIsFocused(false)}
          placeholder={
            taskActive
              ? 'AI 正在思考...'
              : currentTaskSessionId && currentSessionId !== currentTaskSessionId
                ? '当前有任务运行中，可继续输入草稿'
                : '描述你想做什么，或输入 / 命令'
          }
        />

        {/* 底部工具栏 - 发送按钮和提示 */}
        <div
          style={{
            position: 'absolute',
            bottom: '6px',
            left: '8px',
            right: '8px',
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            pointerEvents: 'none',
          }}
        >
          {/* 左侧组：权限模式切换 + 斜杠提示（容器锚定下拉并承接点外关闭） */}
          <div ref={permMenuRef} style={{ position: 'relative', display: 'flex', alignItems: 'center', gap: '6px', pointerEvents: 'auto' }}>
            <button
              onClick={() => { setShowPermMenu(!showPermMenu); setShowModelMenu(false) }}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '3px',
                padding: '2px 6px',
                border: '1px solid var(--border)',
                borderRadius: 'var(--radius-sm)',
                background: 'transparent',
                color: permissionMode === 'full_access' ? 'var(--warning)' : 'var(--text-tertiary)',
                fontSize: '10px',
                fontFamily: 'var(--font-ui)',
                fontWeight: 500,
                cursor: 'pointer',
                transition: 'all var(--transition-fast)',
                whiteSpace: 'nowrap',
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.borderColor = 'var(--border-strong)'
                e.currentTarget.style.color = permissionMode === 'full_access' ? 'var(--warning)' : 'var(--text-secondary)'
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.borderColor = 'var(--border)'
                e.currentTarget.style.color = permissionMode === 'full_access' ? 'var(--warning)' : 'var(--text-tertiary)'
              }}
            >
              {permissionMode === 'full_access' ? '完全访问' : '自动编辑'}
              <svg width="8" height="8" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="m6 9 6 6 6-6" />
              </svg>
            </button>
            {/* 权限模式下拉菜单 */}
            {showPermMenu && (
              <div
                style={{
                  position: 'absolute',
                  bottom: '100%',
                  left: 0,
                  marginBottom: '4px',
                  backgroundColor: 'var(--bg-elevated)',
                  border: '1px solid var(--border-strong)',
                  borderRadius: 'var(--radius-md)',
                  boxShadow: 'var(--shadow-md)',
                  zIndex: 20,
                  minWidth: '240px',
                  overflow: 'hidden',
                }}
              >
                {/* 自动编辑选项 */}
                <div
                  onClick={() => { onPermissionModeChange('default'); setShowPermMenu(false) }}
                  style={{
                    padding: '8px 10px',
                    cursor: 'pointer',
                    backgroundColor: permissionMode === 'default' ? 'var(--selected-bg)' : 'transparent',
                    transition: 'background var(--transition-fast)',
                  }}
                  onMouseEnter={(e) => {
                    if (permissionMode !== 'default') e.currentTarget.style.backgroundColor = 'var(--hover-bg)'
                  }}
                  onMouseLeave={(e) => {
                    if (permissionMode !== 'default') e.currentTarget.style.backgroundColor = 'transparent'
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                    <span style={{ color: 'var(--text-primary)', fontSize: '12px', fontWeight: 500, fontFamily: 'var(--font-ui)' }}>
                      自动编辑
                    </span>
                    {permissionMode === 'default' && (
                      <span style={{ color: 'var(--text-primary)', fontSize: '11px' }}>✓</span>
                    )}
                  </div>
                  <div style={{ color: 'var(--text-tertiary)', fontSize: '10px', marginTop: '2px', fontFamily: 'var(--font-ui)' }}>
                    只读放行、编辑放行，删文件和敏感操作才确认
                  </div>
                </div>
                {/* 完全访问选项 */}
                <div
                  onClick={() => { onPermissionModeChange('full_access'); setShowPermMenu(false) }}
                  style={{
                    padding: '8px 10px',
                    cursor: 'pointer',
                    backgroundColor: permissionMode === 'full_access' ? 'var(--warning-soft)' : 'transparent',
                    transition: 'background var(--transition-fast)',
                    borderTop: '1px solid var(--border-subtle)',
                  }}
                  onMouseEnter={(e) => {
                    if (permissionMode !== 'full_access') e.currentTarget.style.backgroundColor = 'var(--hover-bg)'
                  }}
                  onMouseLeave={(e) => {
                    if (permissionMode !== 'full_access') e.currentTarget.style.backgroundColor = 'transparent'
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                    <span style={{ color: 'var(--text-primary)', fontSize: '12px', fontWeight: 500, fontFamily: 'var(--font-ui)' }}>
                      完全访问
                    </span>
                    {permissionMode === 'full_access' && (
                      <span style={{ color: 'var(--warning)', fontSize: '11px' }}>✓</span>
                    )}
                  </div>
                  <div style={{ color: 'var(--text-tertiary)', fontSize: '10px', marginTop: '2px', fontFamily: 'var(--font-ui)' }}>
                    全部放行，除非模型主动询问用户
                  </div>
                </div>
              </div>
            )}
            {/* 斜杠输入时的命令补全提示 */}
            {text.startsWith('/') && (
              <span
                style={{
                  fontSize: '9px',
                  color: 'var(--text-tertiary)',
                  fontFamily: 'var(--font-mono)',
                  letterSpacing: '0.3px',
                }}
              >
                Tab 选中命令
              </span>
            )}
          </div>
          {/* 右侧组：进度圈 + 模型 + 发送/停止（整组靠右，圈在组首） */}
          <div style={{ display: 'flex', alignItems: 'center', gap: '6px', pointerEvents: 'auto' }}>
            {/* 上下文用量：悬停进度圈即显示「上下文容量」面板（右缘锚定，防溢出窗口） */}
            <div
              style={{ position: 'relative', display: 'flex', alignItems: 'center' }}
              onMouseEnter={() => {
                if (contextPanelHideTimer.current) {
                  window.clearTimeout(contextPanelHideTimer.current)
                  contextPanelHideTimer.current = undefined
                }
                setShowContextPanel(true)
              }}
              onMouseLeave={() => {
                if (contextPanelHideTimer.current) window.clearTimeout(contextPanelHideTimer.current)
                contextPanelHideTimer.current = window.setTimeout(() => {
                  setShowContextPanel(false)
                  contextPanelHideTimer.current = undefined
                }, 150)
              }}
            >
              <button
                aria-label={`上下文 ${tokenUsage.last_prompt_tokens.toLocaleString()} / ${contextWindow.toLocaleString()}（${contextPercent.toFixed(1)}%）`}
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  padding: '2px',
                  border: 'none',
                  borderRadius: '50%',
                  background: showContextPanel ? 'var(--hover-bg)' : 'transparent',
                  cursor: 'default',
                  flexShrink: 0,
                  transition: 'background var(--transition-fast)',
                }}
              >
                {/* 弧长即占比、起点 12 点，>80% 变红 */}
                <svg width="14" height="14" viewBox="0 0 14 14" style={{ transform: 'rotate(-90deg)' }}>
                  <circle cx="7" cy="7" r="5.5" fill="none" stroke="var(--border)" strokeWidth="2.5" />
                  <circle
                    cx="7"
                    cy="7"
                    r="5.5"
                    fill="none"
                    stroke={contextPercent > 80 ? 'var(--error)' : 'var(--text-tertiary)'}
                    strokeWidth="2.5"
                    strokeLinecap="round"
                    strokeDasharray={`${(contextPercent / 100) * 2 * Math.PI * 5.5} ${2 * Math.PI * 5.5}`}
                    style={{ transition: 'stroke-dasharray 0.3s' }}
                  />
                </svg>
              </button>
              {/* 「上下文容量」面板：进度条 + 分类占比 + 平均缓存命中率 + 压缩入口 */}
              {showContextPanel && (
                <div
                  style={{
                    position: 'absolute',
                    bottom: '100%',
                    right: 0,
                    marginBottom: '8px',
                    width: '300px',
                    padding: '14px',
                    backgroundColor: 'var(--bg-elevated)',
                    border: '1px solid var(--border-strong)',
                    borderRadius: 'var(--radius-lg)',
                    boxShadow: 'var(--shadow-md)',
                    backdropFilter: 'blur(8px)',
                    zIndex: 20,
                    fontFamily: 'var(--font-ui)',
                  }}
                >
                  {/* 标题行：已用/窗口（真实 usage 口径）+ 百分比 */}
                  <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', marginBottom: '10px' }}>
                    <span style={{ color: 'var(--text-primary)', fontSize: '14px', fontWeight: 600 }}>
                      上下文容量
                    </span>
                    <span style={{ color: 'var(--text-secondary)', fontSize: '12px', fontFamily: 'var(--font-mono)' }}>
                      {formatWan(tokenUsage.last_prompt_tokens)} / {formatWan(contextWindow)}（{contextPercent.toFixed(1)}%）
                    </span>
                  </div>
                  {/* 横向进度条（>80% 变红） */}
                  <div style={{ height: '6px', borderRadius: '3px', background: 'var(--border)', overflow: 'hidden', marginBottom: '12px' }}>
                    <div
                      style={{
                        height: '100%',
                        width: `${Math.max(contextPercent, 1)}%`,
                        borderRadius: '3px',
                        background: contextPercent > 80 ? 'var(--error)' : '#5b9dff',
                        transition: 'width 0.3s',
                      }}
                    />
                  </div>
                  {/* 分类占比行：色点 + 名称 + 占比（估算口径，按 token 降序） */}
                  {breakdownRows.length > 0 ? (
                    breakdownRows.map(([key, value]) => {
                      const meta = CONTEXT_CATEGORY_META[key] || { label: key, color: 'var(--text-tertiary)' }
                      const pct = breakdownTotal > 0 ? (value / breakdownTotal) * 100 : 0
                      return (
                        <div
                          key={key}
                          style={{
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'space-between',
                            padding: '4px 0',
                          }}
                        >
                          <span style={{ display: 'flex', alignItems: 'center', gap: '8px', color: 'var(--text-secondary)', fontSize: '12px' }}>
                            <span style={{ width: '8px', height: '8px', borderRadius: '50%', background: meta.color, flexShrink: 0 }} />
                            {meta.label}
                          </span>
                          <span style={{ color: 'var(--text-primary)', fontSize: '12px', fontFamily: 'var(--font-mono)' }}>
                            {pct.toFixed(1)}%
                          </span>
                        </div>
                      )
                    })
                  ) : (
                    <div style={{ color: 'var(--text-tertiary)', fontSize: '12px', padding: '4px 0' }}>
                      暂无分类数据，发起一轮对话后显示
                    </div>
                  )}
                  {/* 平均缓存命中率 */}
                  <div
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'space-between',
                      padding: '4px 0',
                      marginTop: '4px',
                      borderTop: '1px solid var(--border-subtle)',
                    }}
                  >
                    <span style={{ color: 'var(--text-secondary)', fontSize: '12px' }}>平均缓存命中率</span>
                    <span style={{ color: 'var(--text-primary)', fontSize: '12px', fontFamily: 'var(--font-mono)' }}>
                      {cacheHitRate !== null ? `${cacheHitRate.toFixed(1)}%` : '—'}
                    </span>
                  </div>
                  {/* 压缩入口：复用 /compact 斜杠命令链路 */}
                  <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: '10px' }}>
                    <button
                      onClick={() => {
                        setShowContextPanel(false)
                        onSend('/compact')
                      }}
                      disabled={taskActive}
                      title={taskActive ? '任务运行中，无法压缩' : '压缩当前对话上下文'}
                      style={{
                        padding: '4px 12px',
                        border: '1px solid var(--border-strong)',
                        borderRadius: 'var(--radius-sm)',
                        background: 'transparent',
                        color: taskActive ? 'var(--text-tertiary)' : 'var(--text-secondary)',
                        fontSize: '11px',
                        fontFamily: 'var(--font-ui)',
                        fontWeight: 500,
                        cursor: taskActive ? 'default' : 'pointer',
                        opacity: taskActive ? 0.6 : 1,
                        transition: 'all var(--transition-fast)',
                      }}
                      onMouseEnter={(e) => {
                        if (!taskActive) {
                          e.currentTarget.style.backgroundColor = 'var(--hover-bg)'
                          e.currentTarget.style.color = 'var(--text-primary)'
                        }
                      }}
                      onMouseLeave={(e) => {
                        if (!taskActive) {
                          e.currentTarget.style.backgroundColor = 'transparent'
                          e.currentTarget.style.color = 'var(--text-secondary)'
                        }
                      }}
                    >
                      压缩
                    </button>
                  </div>
                </div>
              )}
            </div>
            {/* 模型快速切换控件 */}
            <div ref={modelMenuRef} style={{ position: 'relative', display: 'flex', alignItems: 'center' }}>
              <button
                onClick={() => {
                  if (providers.length === 0) return
                  setShowModelMenu(!showModelMenu)
                  setShowPermMenu(false)
                }}
                disabled={switching}
                title={switching ? '切换中...' : '切换模型'}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '3px',
                  padding: '2px 6px',
                  border: '1px solid var(--border)',
                  borderRadius: 'var(--radius-sm)',
                  background: 'transparent',
                  color: providers.length > 0 ? 'var(--text-secondary)' : 'var(--text-tertiary)',
                  fontSize: '10px',
                  fontFamily: 'var(--font-ui)',
                  fontWeight: 500,
                  cursor: switching ? 'wait' : 'pointer',
                  transition: 'all var(--transition-fast)',
                  whiteSpace: 'nowrap',
                  maxWidth: '200px',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  opacity: switching ? 0.6 : 1,
                }}
                onMouseEnter={(e) => {
                  if (!switching) {
                    e.currentTarget.style.borderColor = 'var(--border-strong)'
                    e.currentTarget.style.color = 'var(--text-primary)'
                  }
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.borderColor = 'var(--border)'
                  e.currentTarget.style.color = providers.length > 0 ? 'var(--text-secondary)' : 'var(--text-tertiary)'
                }}
              >
                {switching ? '切换中...' : modelDisplayText}
                {providers.length > 0 && !switching && (
                  <svg width="8" height="8" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0 }}>
                    <path d="m6 9 6 6 6-6" />
                  </svg>
                )}
              </button>
              {/* 模型选择下拉列表 - 按供应商分组（右缘锚定，防靠右排布后溢出窗口） */}
              {showModelMenu && (
                <div
                  style={{
                    position: 'absolute',
                    bottom: '100%',
                    right: 0,
                    marginBottom: '4px',
                    backgroundColor: 'var(--bg-elevated)',
                    border: '1px solid var(--border-strong)',
                    borderRadius: 'var(--radius-md)',
                    boxShadow: 'var(--shadow-md)',
                    backdropFilter: 'blur(8px)',
                    zIndex: 20,
                    minWidth: '260px',
                    maxHeight: '300px',
                    overflowY: 'auto',
                  }}
                >
                  {providers.map((provider, pIdx) => (
                    <div key={provider.id}>
                      {/* 供应商分组标题 */}
                      <div
                        style={{
                          padding: '6px 10px 4px',
                          fontSize: '10px',
                          fontWeight: 600,
                          color: 'var(--text-tertiary)',
                          fontFamily: 'var(--font-ui)',
                          backgroundColor: 'var(--bg-base)',
                          borderTop: pIdx > 0 ? '1px solid var(--border-subtle)' : 'none',
                        }}
                      >
                        {provider.name}
                      </div>
                      {/* 该供应商下的模型列表 */}
                      {provider.models.map(model => {
                        const isActive = provider.id === activeProvider && model.model_id === activeModel
                        return (
                          <div
                            key={model.model_id}
                            onClick={() => handleModelSelect(provider.id, model.model_id)}
                            style={{
                              padding: '6px 10px',
                              cursor: 'pointer',
                              backgroundColor: isActive ? 'var(--selected-bg)' : 'transparent',
                              transition: 'background var(--transition-fast)',
                            }}
                            onMouseEnter={(e) => {
                              if (!isActive) e.currentTarget.style.backgroundColor = 'var(--hover-bg)'
                            }}
                            onMouseLeave={(e) => {
                              if (!isActive) e.currentTarget.style.backgroundColor = 'transparent'
                            }}
                          >
                            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                              <span style={{
                                color: 'var(--text-primary)',
                                fontSize: '12px',
                                fontFamily: 'var(--font-mono)',
                                fontWeight: 500,
                              }}>
                                {model.model_id}
                              </span>
                              {isActive && (
                                <span style={{ color: 'var(--text-primary)', fontSize: '11px' }}>✓</span>
                              )}
                            </div>
                            {model.context_window > 0 && (
                              <div style={{ color: 'var(--text-tertiary)', fontSize: '10px', marginTop: '2px', fontFamily: 'var(--font-ui)' }}>
                                上下文窗口 {model.context_window.toLocaleString()}
                              </div>
                            )}
                          </div>
                        )
                      })}
                    </div>
                  ))}
                </div>
              )}
            </div>
            {taskActive ? (
            // 任务进行中（前台流式或本会话后台任务）：显示停止按钮
            <button
              onClick={onStop}
              title="停止生成"
              style={{
                pointerEvents: 'auto',
                padding: '4px 10px',
                border: '1px solid var(--error)',
                borderRadius: 'var(--radius-sm)',
                background: 'var(--error-soft)',
                color: 'var(--error)',
                fontSize: '11px',
                fontFamily: 'var(--font-ui)',
                fontWeight: 600,
                cursor: 'pointer',
                transition: 'all var(--transition-fast)',
                display: 'flex',
                alignItems: 'center',
                gap: '4px',
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.background = 'var(--error)'
                e.currentTarget.style.color = 'var(--button-primary-text)'
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.background = 'var(--error-soft)'
                e.currentTarget.style.color = 'var(--error)'
              }}
            >
              停止
              <svg width="9" height="9" viewBox="0 0 24 24" fill="currentColor">
                <rect x="6" y="6" width="12" height="12" rx="1.5" />
              </svg>
            </button>
          ) : (
            // 非流式：显示发送按钮
            <button
              onClick={handleSend}
              disabled={!text.trim()}
              style={{
                pointerEvents: 'auto',
                padding: '4px 10px',
                border: 'none',
                borderRadius: 'var(--radius-sm)',
                background: text.trim()
                  ? 'var(--button-primary-bg)'
                  : 'var(--bg-elevated)',
                color: text.trim() ? 'var(--button-primary-text)' : 'var(--text-tertiary)',
                fontSize: '11px',
                fontFamily: 'var(--font-ui)',
                fontWeight: 600,
                cursor: text.trim() ? 'pointer' : 'default',
                transition: 'all var(--transition-fast)',
                display: 'flex',
                alignItems: 'center',
                gap: '4px',
              }}
              onMouseEnter={(e) => {
                if (text.trim()) e.currentTarget.style.background = 'var(--button-primary-bg-hover)'
              }}
              onMouseLeave={(e) => {
                if (text.trim()) e.currentTarget.style.background = 'var(--button-primary-bg)'
              }}
            >
              发送
              <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <path d="M5 12h14M13 5l7 7-7 7" />
              </svg>
            </button>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

export default ChatInput
