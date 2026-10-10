import { Component, type CSSProperties, type ErrorInfo, type ReactNode } from 'react'

interface ErrorBoundaryProps {
  children: ReactNode
  // 出错区块名（如「智能体」），展示在兜底文案里
  label?: string
  // 变化时自动清错重挂子树（如切换会话、重新激活标签）
  resetKey?: unknown
  // 重试按钮走整页重载而非清错重挂：根节点兜底用（子树状态可能已不可信）
  reloadOnRetry?: boolean
}

interface ErrorBoundaryState {
  error: Error | null
}

const boxStyle: CSSProperties = {
  display: 'flex',
  flexDirection: 'column',
  alignItems: 'center',
  justifyContent: 'center',
  gap: '10px',
  height: '100%',
  padding: '24px',
  textAlign: 'center',
}

const btnStyle: CSSProperties = {
  border: '1px solid var(--border)',
  background: 'transparent',
  color: 'var(--text-primary)',
  cursor: 'pointer',
  padding: '4px 14px',
  borderRadius: 'var(--radius-sm)',
  fontSize: '12px',
  fontFamily: 'var(--font-ui)',
}

// 渲染期异常兜底边界：没有边界时 React 会卸掉整棵根树、窗口直接黑屏。
// 工具面板各挂一个边界，单面板崩溃只影响自己；根节点再挂一个做最后防线
export default class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // 崩溃现场留在控制台，界面只给可读摘要
    console.error('[ErrorBoundary]', this.props.label ?? 'root', error, info.componentStack)
  }

  // resetKey 变化（切会话/重开标签）时清错重挂，避免旧异常永久占屏
  componentDidUpdate(prev: ErrorBoundaryProps) {
    if (this.state.error && prev.resetKey !== this.props.resetKey) {
      this.setState({ error: null })
    }
  }

  render() {
    const { error } = this.state
    if (!error) return this.props.children
    const retry = () => {
      if (this.props.reloadOnRetry) window.location.reload()
      else this.setState({ error: null })
    }
    return (
      <div style={boxStyle}>
        <span style={{ fontSize: '13px', color: 'var(--text-primary)', fontFamily: 'var(--font-ui)' }}>
          {this.props.label ? `${this.props.label}面板渲染出错` : '界面渲染出错'}
        </span>
        <span
          style={{
            fontSize: '11px',
            color: 'var(--text-tertiary)',
            fontFamily: 'var(--font-mono)',
            maxWidth: '420px',
            wordBreak: 'break-all',
          }}
        >
          {error.message}
        </span>
        <button onClick={retry} style={btnStyle}>
          {this.props.reloadOnRetry ? '重新加载' : '重试'}
        </button>
      </div>
    )
  }
}
