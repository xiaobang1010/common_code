import { useEffect, useRef } from 'react'
import { Terminal as XTerm } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'

// 集成终端：用 xterm 创建终端实例，当前后端暂无终端 WebSocket 接口，
// 先做 UI 占位，初始化后显示红色提示文字。
function Terminal() {
  // 终端挂载的容器
  const containerRef = useRef<HTMLDivElement>(null)
  // 终端实例，存到 ref 以便卸载时清理
  const termRef = useRef<XTerm | null>(null)

  useEffect(() => {
    if (!containerRef.current) return

    // 创建终端实例，深色主题适配整体风格
    const term = new XTerm({
      fontSize: 13,
      fontFamily: 'Consolas, "Courier New", monospace',
      theme: {
        background: '#1e1e1e',
        foreground: '#d4d4d4',
      },
      cursorBlink: true,
      disableStdin: true, // 后端无接口，先禁用输入
    })
    const fitAddon = new FitAddon()
    term.loadAddon(fitAddon)
    term.open(containerRef.current)
    fitAddon.fit()

    // 后端暂无终端接口，显示红色提示
    term.writeln('\x1b[31m终端功能开发中 - 后端暂无终端接口\x1b[0m')

    termRef.current = term

    // 组件卸载时释放终端实例，避免内存泄漏
    return () => {
      term.dispose()
      termRef.current = null
    }
  }, [])

  return (
    <div
      ref={containerRef}
      style={{
        width: '100%',
        height: '100%',
        backgroundColor: '#1e1e1e',
        padding: '4px 8px',
        overflow: 'hidden',
      }}
    />
  )
}

export default Terminal
