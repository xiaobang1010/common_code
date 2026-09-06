import { forwardRef, useImperativeHandle, useRef, type KeyboardEvent as ReactKeyboardEvent } from 'react'

// 文件引用 chip 在 DOM 上的标识属性：值为工作区相对路径
const REF_ATTR = 'data-file-ref'

// 把编辑区 DOM 序列化为纯文本：chip 还原成 [文件名](./相对路径) 的
// Markdown 链接、换行还原成 \n。发送与补全判断都以这份文本为准
function serialize(root: HTMLElement): string {
  let out = ''
  const walk = (node: Node) => {
    if (node.nodeType === Node.TEXT_NODE) {
      out += node.textContent ?? ''
      return
    }
    if (node.nodeType !== Node.ELEMENT_NODE) return
    const el = node as HTMLElement
    const ref = el.getAttribute(REF_ATTR)
    if (ref) {
      out += `[${ref.split('/').pop() || ref}](./${ref})`
      return
    }
    if (el.tagName === 'BR') {
      out += '\n'
      return
    }
    // 块级子节点（回车换行产生的 div）在边界补换行，保持行结构
    const isBlock = el.tagName === 'DIV' || el.tagName === 'P'
    if (isBlock && out && !out.endsWith('\n')) out += '\n'
    el.childNodes.forEach(walk)
  }
  root.childNodes.forEach(walk)
  return out
}

// 构造一个不可编辑的文件引用 chip：图标 + 文件名 + × 移除钮
function makeChip(path: string, onRemove: () => void): HTMLSpanElement {
  const chip = document.createElement('span')
  chip.contentEditable = 'false'
  chip.setAttribute(REF_ATTR, path)
  chip.title = path
  chip.className = 'chat-ref-chip'

  // 文件小图标
  const icon = document.createElementNS('http://www.w3.org/2000/svg', 'svg')
  icon.setAttribute('width', '10')
  icon.setAttribute('height', '10')
  icon.setAttribute('viewBox', '0 0 24 24')
  icon.setAttribute('fill', 'none')
  icon.setAttribute('stroke', 'currentColor')
  icon.setAttribute('stroke-width', '1.8')
  icon.setAttribute('stroke-linecap', 'round')
  icon.setAttribute('stroke-linejoin', 'round')
  const p1 = document.createElementNS('http://www.w3.org/2000/svg', 'path')
  p1.setAttribute('d', 'M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z')
  const p2 = document.createElementNS('http://www.w3.org/2000/svg', 'path')
  p2.setAttribute('d', 'M14 2v6h6')
  icon.appendChild(p1)
  icon.appendChild(p2)

  const name = document.createElement('span')
  name.textContent = path.split('/').pop() || path

  const close = document.createElement('button')
  close.textContent = '×'
  close.title = '移除引用'
  close.className = 'chat-ref-chip-close'
  // mousedown 先于失焦触发，就地移除并通知外部刷新文本
  close.addEventListener('mousedown', (e) => {
    e.preventDefault()
    e.stopPropagation()
    chip.remove()
    onRemove()
  })

  chip.appendChild(icon)
  chip.appendChild(name)
  chip.appendChild(close)
  return chip
}

export interface RichChatInputHandle {
  focus: () => void
  clear: () => void
  // 整体替换为纯文本（命令补全选中时用）
  setText: (text: string) => void
  // 插入文件引用 chip：光标在本输入框内则插在光标处，否则追加到末尾
  insertRef: (path: string) => void
}

interface RichChatInputProps {
  placeholder: string
  disabled: boolean
  // 内容变化时上报序列化文本
  onTextChange: (text: string) => void
  // Enter 发送
  onSubmit: () => void
  // 按键先行处理（如补全导航）：返回 true 表示已消费，组件不再处理
  onKeyDown?: (e: ReactKeyboardEvent<HTMLDivElement>) => boolean
  onFocus?: () => void
  onBlur?: () => void
}

// 对话输入框（富文本）：contentEditable 承载文本流，文件引用以不可编辑的
// 内联 chip 混排在文字之间，发送时按位置序列化为 [文件名](./相对路径)
// 的 Markdown 链接，让模型能把引用对应用户意图中的具体指称
const RichChatInput = forwardRef<RichChatInputHandle, RichChatInputProps>(
  function RichChatInput({ placeholder, disabled, onTextChange, onSubmit, onKeyDown, onFocus, onBlur }, ref) {
    const elRef = useRef<HTMLDivElement>(null)

    const emitChange = () => {
      const el = elRef.current
      if (!el) return
      const text = serialize(el)
      // 内容清空时把浏览器残留的空 div/br 一并清掉，保证占位符（:empty）恢复显示
      if (!text && el.innerHTML) el.innerHTML = ''
      onTextChange(text)
    }

    useImperativeHandle(ref, () => ({
      focus: () => elRef.current?.focus(),
      clear: () => {
        const el = elRef.current
        if (!el) return
        el.innerHTML = ''
        onTextChange('')
      },
      setText: (text: string) => {
        const el = elRef.current
        if (!el) return
        el.textContent = text
        onTextChange(text)
      },
      insertRef: (path: string) => {
        const el = elRef.current
        if (!el) return
        const chip = makeChip(path, emitChange)
        const sel = window.getSelection()
        // 光标还在本输入框内（如边打字边右键添加）：插在光标处并落在 chip 之后
        if (sel && sel.rangeCount > 0 && el.contains(sel.anchorNode)) {
          const range = sel.getRangeAt(0)
          range.deleteContents()
          range.insertNode(chip)
          range.setStartAfter(chip)
          range.collapse(true)
          sel.removeAllRanges()
          sel.addRange(range)
        } else {
          el.appendChild(chip)
        }
        el.focus()
        emitChange()
      },
    }))

    return (
      <div
        ref={elRef}
        className="chat-rich-input"
        contentEditable={!disabled}
        data-placeholder={placeholder}
        role="textbox"
        aria-multiline="true"
        spellCheck={false}
        style={{ opacity: disabled ? 0.6 : 1 }}
        onFocus={onFocus}
        onBlur={onBlur}
        onInput={emitChange}
        onPaste={(e) => {
          // 粘贴一律降级为纯文本，避免外部 HTML 混入编辑区
          e.preventDefault()
          const text = e.clipboardData.getData('text/plain')
          if (!text) return
          document.execCommand('insertText', false, text)
        }}
        onKeyDown={(e) => {
          if (onKeyDown?.(e)) return
          // 输入法组词期间的回车不发送
          if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault()
            onSubmit()
          }
        }}
      />
    )
  },
)

export default RichChatInput
