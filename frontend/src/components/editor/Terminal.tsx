import { useEffect, useRef } from 'react'
import { Terminal as XTerm } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'

// 终端 IPC 接口类型
interface ElectronTerminalAPI {
  // create 返回 { id, shell }：shell 名供终端标签标题展示
  create: (cwd?: string) => Promise<{ id: string; shell: string }>
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

interface TerminalProps {
  // 父组件分配的唯一实例 id，变化时会重新创建终端
  instanceId: string
  // 终端创建完成后的回调，把 pty id 与 shell 名回传给父组件管理
  onReady?: (ptyId: string, shell: string) => void
}

// 单个终端实例：通过 Electron 主进程的 node-pty 接入真实 PowerShell。
// 开发模式（浏览器直连 Vite，没有 preload）下降级显示提示。
// 父组件通过 instanceId 控制何时重新创建（切换 tab 时复用同一个 XTerm 容器）
function Terminal({ instanceId, onReady }: TerminalProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const termRef = useRef<XTerm | null>(null)

  useEffect(() => {
    if (!containerRef.current) return

    const api = getTerminalAPI()
    const container = containerRef.current

    // 创建终端实例，深色主题适配整体风格
    const term = new XTerm({
      fontSize: 13,
      fontFamily: 'JetBrains Mono, Consolas, "Courier New", monospace',
      theme: {
        background: '#0f1115',
        foreground: '#e6e9ef',
        cursor: '#e6e9ef',
        selectionBackground: 'rgba(255, 255, 255, 0.22)',
      },
      cursorBlink: true,
    })
    const fitAddon = new FitAddon()
    term.loadAddon(fitAddon)
    term.open(container)
    fitAddon.fit()

    termRef.current = term

    // 没有 preload 接口（开发模式浏览器访问），降级提示
    if (!api) {
      term.writeln('\x1b[33m当前为开发模式（浏览器直连），终端不可用。请在 Electron 中运行以使用集成终端。\x1b[0m')
      return () => {
        term.dispose()
        termRef.current = null
      }
    }

    let disposed = false
    let cleanupOutput: (() => void) | undefined
    let ptyId: string | undefined

    api.create().then((res) => {
      if (disposed) {
        api.dispose(res.id)
        return
      }
      ptyId = res.id
      onReady?.(res.id, res.shell)

      // 转发用户输入到 pty
      term.onData((data) => {
        api.input(res.id, data)
      })

      // 转发尺寸变化到 pty
      term.onResize(({ cols, rows }) => {
        api.resize(res.id, cols, rows)
      })

      // 接收 pty 输出
      cleanupOutput = api.onOutput((outputId, data) => {
        if (outputId === res.id) {
          term.write(data)
        }
      })

      api.resize(res.id, term.cols, term.rows)
    })

    // 容器尺寸变化时重新拟合
    const resizeObserver = new ResizeObserver(() => {
      fitAddon.fit()
    })
    resizeObserver.observe(container)

    return () => {
      disposed = true
      resizeObserver.disconnect()
      if (cleanupOutput) cleanupOutput()
      if (ptyId) api.dispose(ptyId)
      term.dispose()
      termRef.current = null
    }
    // instanceId 变化时整个终端重建
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [instanceId])

  return (
    <div
      ref={containerRef}
      style={{
        width: '100%',
        height: '100%',
        backgroundColor: '#0f1115',
        padding: '4px 8px',
        overflow: 'hidden',
      }}
    />
  )
}

export default Terminal
