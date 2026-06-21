import { useState, useRef, useEffect } from 'react'

interface Props {
  onSend: (prompt: string) => void
  disabled: boolean
}

// 硬编码的斜杠命令列表
const COMMANDS = [
  { name: '/help', desc: '显示帮助' },
  { name: '/clear', desc: '清空对话' },
  { name: '/compact', desc: '压缩历史' },
  { name: '/config', desc: '查看配置' },
  { name: '/model', desc: '切换模型' },
  { name: '/cost', desc: '查看成本' },
  { name: '/exit', desc: '退出' },
  { name: '/spec', desc: '查看规格' },
]

function ChatInput({ onSend, disabled }: Props) {
  const [value, setValue] = useState('')
  const [showCommands, setShowCommands] = useState(false)
  const [selectedIdx, setSelectedIdx] = useState(0)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  // 根据当前输入过滤命令
  const filteredCommands = COMMANDS.filter(c => c.name.startsWith(value))

  // 输入变化时决定是否显示命令补全
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
      // 命令补全模式：上下键选择，Enter 选中，Esc 关闭
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
        // 选中的命令填入输入框
        setValue(filteredCommands[selectedIdx].name + ' ')
        setShowCommands(false)
        textareaRef.current?.focus()
        return
      }
    } else {
      // 普通模式：Enter 发送，Shift+Enter 换行
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault()
        handleSend()
      }
    }
  }

  return (
    <div style={{ position: 'relative' }}>
      {/* 命令补全下拉列表 */}
      {showCommands && (
        <div
          style={{
            position: 'absolute',
            bottom: '100%',
            left: 0,
            right: 0,
            backgroundColor: 'var(--bg-tertiary)',
            border: '1px solid var(--border)',
            borderRadius: '4px',
            marginBottom: '4px',
            maxHeight: '200px',
            overflowY: 'auto',
            zIndex: 10,
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
                padding: '6px 12px',
                cursor: 'pointer',
                backgroundColor: idx === selectedIdx ? 'var(--accent)' : 'transparent',
                color: idx === selectedIdx ? '#fff' : 'var(--text-primary)',
                fontSize: '13px',
                display: 'flex',
                justifyContent: 'space-between',
              }}
            >
              <span>{cmd.name}</span>
              <span style={{ color: 'var(--text-secondary)', fontSize: '11px' }}>
                {cmd.desc}
              </span>
            </div>
          ))}
        </div>
      )}
      <textarea
        ref={textareaRef}
        value={value}
        onChange={e => setValue(e.target.value)}
        onKeyDown={handleKeyDown}
        disabled={disabled}
        placeholder="输入消息或 /命令..."
        rows={2}
        style={{
          width: '100%',
          resize: 'none',
          backgroundColor: 'var(--bg-tertiary)',
          border: '1px solid var(--border)',
          borderRadius: '4px',
          padding: '8px',
          color: 'var(--text-primary)',
          fontSize: '13px',
          fontFamily: 'inherit',
          outline: 'none',
          opacity: disabled ? 0.5 : 1,
        }}
      />
    </div>
  )
}

export default ChatInput
