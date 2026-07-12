import { useState, useRef, useEffect } from 'react'
import type { PermissionRequest } from '../../hooks/useChat'
import { llmApi, type PermissionMode, type CustomLLMProviderInfo } from '../../api/client'
import { useSettingsStore } from '../../stores/useSettingsStore'

interface Props {
  onSend: (prompt: string) => void
  disabled: boolean
  // 是否正在流式输出（用于显示停止按钮）
  isStreaming: boolean
  // 停止当前对话
  onStop: () => void
  // 当前待确认的权限请求，为 null 时不显示卡片
  permissionRequest: PermissionRequest | null
  // 用户做出权限决策时回调
  onResolve: (decision: 'allow' | 'deny' | 'always_allow') => void
  // 当前权限模式
  permissionMode: PermissionMode
  // 切换权限模式
  onPermissionModeChange: (mode: PermissionMode) => void
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
  { name: '/spec', desc: '查看规格' },
]

function ChatInput({ onSend, disabled, isStreaming, onStop, permissionRequest, onResolve, permissionMode, onPermissionModeChange }: Props) {
  const [value, setValue] = useState('')
  const [showCommands, setShowCommands] = useState(false)
  const [selectedIdx, setSelectedIdx] = useState(0)
  const [isFocused, setIsFocused] = useState(false)
  const [commands, setCommands] = useState(BUILTIN_COMMANDS)
  const [showPermMenu, setShowPermMenu] = useState(false)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const permMenuRef = useRef<HTMLDivElement>(null)

  // ---- 模型快速切换相关状态 ----
  // modelVersion 来自 store，外部（如设置面板）切换模型时会变化，触发重新拉取供应商列表
  const modelVersion = useSettingsStore(s => s.modelVersion)
  const notifyModelChanged = useSettingsStore(s => s.notifyModelChanged)
  // 供应商列表和当前激活的供应商/模型
  const [providers, setProviders] = useState<CustomLLMProviderInfo[]>([])
  const [activeProvider, setActiveProvider] = useState<string | null>(null)
  const [activeModel, setActiveModel] = useState<string | null>(null)
  const [showModelMenu, setShowModelMenu] = useState(false)
  const [switching, setSwitching] = useState(false)
  const modelMenuRef = useRef<HTMLDivElement>(null)

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

  const filteredCommands = commands.filter(c => c.name.startsWith(value))

  useEffect(() => {
    if (value.startsWith('/') && filteredCommands.length > 0) {
      setShowCommands(true)
      setSelectedIdx(0)
    } else {
      setShowCommands(false)
    }
  }, [value])

  const handleSend = () => {
    const trimmed = value.trim()
    if (!trimmed || disabled) return
    onSend(trimmed)
    setValue('')
    setShowCommands(false)
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (showCommands) {
      if (e.key === 'ArrowDown') {
        e.preventDefault()
        setSelectedIdx(prev => (prev + 1) % filteredCommands.length)
        return
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault()
        setSelectedIdx(prev => (prev - 1 + filteredCommands.length) % filteredCommands.length)
        return
      }
      if (e.key === 'Escape') {
        e.preventDefault()
        setShowCommands(false)
        return
      }
      if (e.key === 'Enter') {
        e.preventDefault()
        setValue(filteredCommands[selectedIdx].name + ' ')
        setShowCommands(false)
        textareaRef.current?.focus()
        return
      }
    } else {
      // Enter 发送，Shift+Enter 换行，Ctrl/Cmd+Enter 也发送
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault()
        handleSend()
      }
    }
  }

  // 切换模型：调用激活接口 -> 刷新本地状态 -> 广播变更让 StatusBar 更新
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
      // 广播变更，让 StatusBar 等组件更新显示
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

  return (
    <div style={{ position: 'relative' }}>
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
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0 }}>
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
          </div>

          {/* 工具名 - 等宽字体琥珀色 */}
          <div
            style={{
              color: 'var(--accent)',
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
                e.currentTarget.style.backgroundColor = 'rgba(255, 107, 107, 0.1)'
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
            {/* 总是允许 - 描边按钮，hover 琥珀色 */}
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
                e.currentTarget.style.backgroundColor = 'var(--accent-soft)'
                e.currentTarget.style.borderColor = 'var(--accent)'
                e.currentTarget.style.color = 'var(--accent)'
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.backgroundColor = 'transparent'
                e.currentTarget.style.borderColor = 'var(--border-strong)'
                e.currentTarget.style.color = 'var(--text-secondary)'
              }}
            >
              总是允许
            </button>
            {/* 允许 - 琥珀渐变主按钮 */}
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
                background: 'linear-gradient(135deg, var(--accent), #ff7a45)',
                color: '#1a1a1a',
                boxShadow: '0 4px 12px rgba(245, 166, 35, 0.3)',
                transition: 'all var(--transition-fast)',
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.transform = 'translateY(-1px)'
                e.currentTarget.style.boxShadow = '0 6px 16px rgba(245, 166, 35, 0.4)'
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.transform = 'translateY(0)'
                e.currentTarget.style.boxShadow = '0 4px 12px rgba(245, 166, 35, 0.3)'
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
                setValue(cmd.name + ' ')
                setShowCommands(false)
                textareaRef.current?.focus()
              }}
              style={{
                padding: '8px 14px',
                cursor: 'pointer',
                backgroundColor: idx === selectedIdx ? 'var(--accent-soft)' : 'transparent',
                color: 'var(--text-primary)',
                fontSize: '13px',
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                transition: 'background var(--transition-fast)',
              }}
            >
              <span style={{ fontFamily: 'var(--font-mono)', color: idx === selectedIdx ? 'var(--accent)' : 'var(--text-primary)' }}>
                {cmd.name}
              </span>
              <span style={{ color: 'var(--text-tertiary)', fontSize: '11px' }}>
                {cmd.desc}
              </span>
            </div>
          ))}
        </div>
      )}

      {/* 输入框容器 - 聚焦时有发光边框 */}
      <div
        style={{
          position: 'relative',
          borderRadius: 'var(--radius-lg)',
          background: 'var(--bg-tertiary)',
          border: `1px solid ${isFocused ? 'var(--accent)' : 'var(--border)'}`,
          boxShadow: isFocused
            ? '0 0 0 3px var(--accent-soft), 0 0 20px var(--accent-glow)'
            : 'none',
          transition: 'all var(--transition)',
        }}
      >
        <textarea
          ref={textareaRef}
          value={value}
          onChange={e => setValue(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={disabled}
          onFocus={() => setIsFocused(true)}
          onBlur={() => setIsFocused(false)}
          placeholder={disabled ? 'AI 正在思考...' : '描述你想做什么，或输入 / 命令'}
          rows={2}
          style={{
            width: '100%',
            resize: 'none',
            background: 'transparent',
            border: 'none',
            borderRadius: 'var(--radius-lg)',
            padding: '12px 14px 32px',
            color: 'var(--text-primary)',
            fontSize: '14px',
            fontFamily: 'var(--font-ui)',
            lineHeight: 1.5,
            outline: 'none',
            opacity: disabled ? 0.6 : 1,
          }}
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
          <div ref={permMenuRef} style={{ position: 'relative', display: 'flex', alignItems: 'center', gap: '6px', pointerEvents: 'auto' }}>
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
              {/* 模型选择下拉列表 - 按供应商分组 */}
              {showModelMenu && (
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
                              backgroundColor: isActive ? 'var(--accent-soft)' : 'transparent',
                              transition: 'background var(--transition-fast)',
                            }}
                            onMouseEnter={(e) => {
                              if (!isActive) e.currentTarget.style.backgroundColor = 'var(--bg-tertiary)'
                            }}
                            onMouseLeave={(e) => {
                              if (!isActive) e.currentTarget.style.backgroundColor = 'transparent'
                            }}
                          >
                            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                              <span style={{
                                color: isActive ? 'var(--accent)' : 'var(--text-primary)',
                                fontSize: '12px',
                                fontFamily: 'var(--font-mono)',
                                fontWeight: 500,
                              }}>
                                {model.model_id}
                              </span>
                              {isActive && (
                                <span style={{ color: 'var(--accent)', fontSize: '11px' }}>✓</span>
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
            {/* 权限模式切换按钮 */}
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
                color: permissionMode === 'full_access' ? 'var(--accent)' : 'var(--text-tertiary)',
                fontSize: '10px',
                fontFamily: 'var(--font-ui)',
                fontWeight: 500,
                cursor: 'pointer',
                transition: 'all var(--transition-fast)',
                whiteSpace: 'nowrap',
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.borderColor = 'var(--border-strong)'
                e.currentTarget.style.color = permissionMode === 'full_access' ? 'var(--accent)' : 'var(--text-secondary)'
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.borderColor = 'var(--border)'
                e.currentTarget.style.color = permissionMode === 'full_access' ? 'var(--accent)' : 'var(--text-tertiary)'
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
                    backgroundColor: permissionMode === 'default' ? 'var(--accent-soft)' : 'transparent',
                    transition: 'background var(--transition-fast)',
                  }}
                  onMouseEnter={(e) => {
                    if (permissionMode !== 'default') e.currentTarget.style.backgroundColor = 'var(--bg-tertiary)'
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
                      <span style={{ color: 'var(--accent)', fontSize: '11px' }}>✓</span>
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
                    backgroundColor: permissionMode === 'full_access' ? 'var(--accent-soft)' : 'transparent',
                    transition: 'background var(--transition-fast)',
                    borderTop: '1px solid var(--border-subtle)',
                  }}
                  onMouseEnter={(e) => {
                    if (permissionMode !== 'full_access') e.currentTarget.style.backgroundColor = 'var(--bg-tertiary)'
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
                      <span style={{ color: 'var(--accent)', fontSize: '11px' }}>✓</span>
                    )}
                  </div>
                  <div style={{ color: 'var(--text-tertiary)', fontSize: '10px', marginTop: '2px', fontFamily: 'var(--font-ui)' }}>
                    全部放行，除非模型主动询问用户
                  </div>
                </div>
              </div>
            )}
            {/* 保留原来的快捷键提示，更小字体 */}
            <span
              style={{
                fontSize: '9px',
                color: 'var(--text-tertiary)',
                fontFamily: 'var(--font-mono)',
                letterSpacing: '0.3px',
              }}
            >
              {value.startsWith('/') ? 'Tab 选中命令' : 'Shift+Enter 换行'}
            </span>
          </div>
          {isStreaming ? (
            // 流式输出中：显示停止按钮
            <button
              onClick={onStop}
              title="停止生成"
              style={{
                pointerEvents: 'auto',
                padding: '4px 10px',
                border: '1px solid var(--error)',
                borderRadius: 'var(--radius-sm)',
                background: 'rgba(255, 107, 107, 0.1)',
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
                e.currentTarget.style.color = '#fff'
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.background = 'rgba(255, 107, 107, 0.1)'
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
              disabled={disabled || !value.trim()}
              style={{
                pointerEvents: 'auto',
                padding: '4px 10px',
                border: 'none',
                borderRadius: 'var(--radius-sm)',
                background: value.trim() && !disabled
                  ? 'linear-gradient(135deg, var(--accent), #ff7a45)'
                  : 'var(--bg-elevated)',
                color: value.trim() && !disabled ? '#1a1a1a' : 'var(--text-tertiary)',
                fontSize: '11px',
                fontFamily: 'var(--font-ui)',
                fontWeight: 600,
                cursor: value.trim() && !disabled ? 'pointer' : 'default',
                transition: 'all var(--transition-fast)',
                display: 'flex',
                alignItems: 'center',
                gap: '4px',
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
  )
}

export default ChatInput
