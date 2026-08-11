import Terminal from '../../editor/Terminal'

// 面板内终端的固定实例 id（单实例）
const INSPECTOR_TERM_ID = 'inspector-terminal'

// 终端卡：内嵌现有 Terminal 组件（xterm + Electron node-pty）
// 卡片收起时内容区仅 CSS 隐藏不卸载，终端会话保持存活；
// 开发模式（浏览器直连无 preload）下由 Terminal 组件自带降级提示
function TerminalCard() {
  return (
    <div style={{ flex: 1, minHeight: 0, overflow: 'hidden' }}>
      <Terminal instanceId={INSPECTOR_TERM_ID} />
    </div>
  )
}

export default TerminalCard
