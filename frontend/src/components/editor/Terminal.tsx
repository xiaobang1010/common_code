import { useEffect, useRef } from 'react'
import { Terminal as XTerm } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'

// 终端 IPC 接口类型
interface ElectronTerminalAPI {
  create: (cwd?: string) => Promise<string>
  input: (id: string, data: string) => void
  resize: (id: string, cols: number, rows: number) => Promise<void>
  dispose: (id: string) => Promise<void>
  onOutput: (callback: (id: string, data: string) => void) => () => void
}

// 从 window 上取终端 IPC 接口（开发模式浏览器直连时没有 preload，不存在该接口）
function getTerminalAPI(): ElectronTerminalAPI | undefined {
  const w = window as unknown as { electronAPI?: { terminal?: ElectronTerminalAPI } }
  return w.electronAPI?.terminal
}

// 集成终端：通过 Electron 主进程的 node-pty 接入真实 PowerShell。
// 开发模式（浏览器直连 Vite，没有 preload）下降级显示提示。
function Terminal() {
  // 终端挂载的容器
  const containerRef = useRef<HTMLDivElement>(null)
  // 终端实例，存到 ref 以便卸载时清理
  const termRef = useRef<XTerm | null>(null)

  useEffect(() => {
    if (!containerRef.current) return

    const api = getTerminalAPI()
    const container = containerRef.current

    // 创建终端实例，深色主题适配整体风格
    const term = new XTerm({
      fontSize: 13,
      fontFamily: 'Consolas, "Courier New", monospace',
      theme: {
        background: '#1e1e1e',
        foreground: '#d4d4d4',
      },
      cursorBlink: true,
    })
    const fitAddon = new FitAddon()
    term.loadAddon(fitAddon)
    term.open(container)
    fitAddon.fit()

    termRef.current = term

    // 没有 preload 接口（开发模式浏览器访问），降级提示后直接返回
    if (!api) {
      term.writeln('\x1b[33m当前为开发模式（浏览器直连），终端不可用。请在 Electron 中运行以使用集成终端。\x1b[0m')
      return () => {
        term.dispose()
        termRef.current = null
      }
    }

    // 有 preload 接口，创建真实终端
    let disposed = false
    let cleanupOutput: (() => void) | undefined
    let termId: string | undefined

    api.create().then((id) => {
      // 组件已卸载则立刻清理刚创建的终端，避免泄漏
      if (disposed) {
        api.dispose(id)
        return
      }
      termId = id

      // 转发用户输入到 pty
      term.onData((data) => {
        api.input(id, data)
      })

      // 转发尺寸变化到 pty
      term.onResize(({ cols, rows }) => {
        api.resize(id, cols, rows)
      })

      // 接收 pty 输出，只写属于当前组件的终端
      cleanupOutput = api.onOutput((outputId, data) => {
        if (outputId === id) {
          term.write(data)
        }
      })

      // 创建后按当前尺寸同步一次，保证 pty 列数行数与 xterm 一致
      api.resize(id, term.cols, term.rows)
    })

    // 容器尺寸变化时重新拟合，fit 会触发 onResize 进而转发给 pty
    const resizeObserver = new ResizeObserver(() => {
      fitAddon.fit()
    })
    resizeObserver.observe(container)

    return () => {
      disposed = true
      resizeObserver.disconnect()
      if (cleanupOutput) cleanupOutput()
      if (termId) api.dispose(termId)
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
