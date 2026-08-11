import FileTree from '../../sidebar/FileTree'

interface FilesCardProps {
  // 当前工作区路径，null 表示未选择工作区
  workspacePath: string | null
  // 点击文件在编辑器打开
  onFileOpen: (path: string) => void
}

// 文件卡：复用侧边栏的文件树组件，点击文件在编辑器打开
function FilesCard({ workspacePath, onFileOpen }: FilesCardProps) {
  // 未选择工作区时显示占位提示
  if (!workspacePath) {
    return (
      <div
        style={{
          flex: 1,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          color: 'var(--text-tertiary)',
          fontSize: '12px',
        }}
      >
        未选择工作区
      </div>
    )
  }

  return <FileTree onFileOpen={onFileOpen} />
}

export default FilesCard
