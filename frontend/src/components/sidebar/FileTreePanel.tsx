import FileTree from './FileTree'

interface FileTreePanelProps {
  // 「← 返回任务」：回到进入文件树前的侧栏视图
  onBack: () => void
  // 点击文件：交由 App 在右侧面板打开（面板折叠时自动展开）
  onFileOpen: (path: string) => void
  // 工作区显示名（FileTree 头部标题行）
  workspaceName: string
}

// 侧栏文件树视图：左侧栏的第三种形态（项目/分组之外）。
// 头部「返回任务」+ 复用 FileTree（自带搜索过滤、仅显示变更文件、刷新）。
function FileTreePanel({ onBack, onFileOpen, workspaceName }: FileTreePanelProps) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' }}>
      <div
        style={{
          padding: '8px',
          borderBottom: '1px solid var(--border)',
          flexShrink: 0,
          display: 'flex',
          alignItems: 'center',
        }}
      >
        <button
          onClick={onBack}
          title="返回任务列表"
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '6px',
            border: 'none',
            background: 'transparent',
            color: 'var(--text-secondary)',
            fontSize: '12px',
            fontFamily: 'var(--font-ui)',
            cursor: 'pointer',
            padding: '4px 6px',
            borderRadius: 'var(--radius-sm)',
            transition: 'all var(--transition-fast)',
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.color = 'var(--text-primary)'
            e.currentTarget.style.background = 'var(--hover-bg)'
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.color = 'var(--text-secondary)'
            e.currentTarget.style.background = 'transparent'
          }}
        >
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M19 12H5M12 19l-7-7 7-7" />
          </svg>
          返回任务
        </button>
      </div>
      <div style={{ flex: 1, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
        <FileTree onFileOpen={onFileOpen} workspaceName={workspaceName} />
      </div>
    </div>
  )
}

export default FileTreePanel
