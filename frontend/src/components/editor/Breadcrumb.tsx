// 面包屑：显示激活文件的工作区相对路径，长路径中间省略，hover 显示完整路径
interface BreadcrumbProps {
  path: string
}

function Breadcrumb({ path }: BreadcrumbProps) {
  const segments = path.split('/').filter(Boolean)
  // 超过 3 段时中间省略，只保留末尾两段（保证文件名可见）
  const shown = segments.length > 3 ? ['…', ...segments.slice(-2)] : segments

  return (
    <div
      title={path}
      style={{
        height: '24px',
        flexShrink: 0,
        display: 'flex',
        alignItems: 'center',
        gap: '4px',
        padding: '0 12px',
        borderBottom: '1px solid var(--border-subtle)',
        backgroundColor: 'var(--bg-base)',
        overflow: 'hidden',
        whiteSpace: 'nowrap',
        fontSize: '11px',
        fontFamily: 'var(--font-ui)',
        userSelect: 'none',
      }}
    >
      {shown.map((seg, i) => (
        <span key={`${seg}-${i}`} style={{ display: 'flex', alignItems: 'center', gap: '4px', flexShrink: 0 }}>
          {i > 0 && <span style={{ color: 'var(--border-strong)' }}>/</span>}
          <span style={{ color: i === shown.length - 1 ? 'var(--text-primary)' : 'var(--text-tertiary)' }}>{seg}</span>
        </span>
      ))}
    </div>
  )
}

export default Breadcrumb
