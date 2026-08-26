import { useEffect, useState } from 'react'
import { DiffEditor } from '@monaco-editor/react'
import { gitApi, type FileDiffResult } from '../../../api/client'

interface DiffViewProps {
  /** 仓库根相对路径（与审查清单口径一致） */
  path: string
  /** 布局模式由审查工具栏统一下发（默认单栏） */
  sideBySide: boolean
}

// 扩展名到 Monaco 语言标识的映射，仅用于对比视图的高亮
const EXT_LANG: Record<string, string> = {
  '.py': 'python',
  '.js': 'javascript',
  '.ts': 'typescript',
  '.tsx': 'typescript',
  '.jsx': 'javascript',
  '.json': 'json',
  '.md': 'markdown',
  '.html': 'html',
  '.css': 'css',
  '.yaml': 'yaml',
  '.yml': 'yaml',
}

// 从文件路径取 Monaco 语言标识，未识别的走纯文本
function detectLanguage(path: string): string {
  const idx = path.lastIndexOf('.')
  if (idx < 0) return 'plaintext'
  return EXT_LANG[path.slice(idx).toLowerCase()] ?? 'plaintext'
}

// 占位提示（二进制 / 超大 / 加载失败共用样式）
function Placeholder({ text }: { text: string }) {
  return (
    <div
      data-testid="diff-placeholder"
      style={{
        flex: 1,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        fontSize: '12px',
        color: 'var(--text-tertiary)',
        padding: '16px',
        textAlign: 'center',
      }}
    >
      {text}
    </div>
  )
}

// 行内展开的单文件前后对比视图：HEAD 版本 vs 工作区当前版本。
// 只读展示，布局模式（单栏/双栏）由审查工具栏统一下发。
function DiffView({ path, sideBySide }: DiffViewProps) {
  const [result, setResult] = useState<FileDiffResult | null>(null)
  const [loadError, setLoadError] = useState('')

  useEffect(() => {
    let cancelled = false
    setResult(null)
    setLoadError('')
    gitApi
      .diff(path)
      .then((data) => {
        if (!cancelled) setResult(data)
      })
      .catch((e) => {
        if (!cancelled) setLoadError(e instanceof Error ? e.message : String(e))
      })
    return () => {
      cancelled = true
    }
  }, [path])

  return (
    <div
      data-testid="review-inline-diff"
      style={{ display: 'flex', flexDirection: 'column', height: '380px', borderTop: '1px solid var(--border)' }}
    >
      {/* 内容区：加载中 / 占位提示 / 对比编辑器 */}
      {!result && !loadError ? (
        <Placeholder text="加载中…" />
      ) : loadError ? (
        <Placeholder text={`加载失败：${loadError}`} />
      ) : result?.error ? (
        <Placeholder text={`无法显示对比：${result.error}`} />
      ) : result?.binary ? (
        <Placeholder text="二进制文件，无法对比内容" />
      ) : result?.tooLarge ? (
        <Placeholder text="文件过大（超过 1MB），已跳过内容对比" />
      ) : (
        <DiffEditor
          key={path}
          height="100%"
          theme="vs-dark"
          language={detectLanguage(path)}
          original={result?.oldText ?? ''}
          modified={result?.newText ?? ''}
          options={{
            readOnly: true,
            renderSideBySide: sideBySide,
            useInlineViewWhenSpaceIsLimited: false,
            minimap: { enabled: false },
            fontSize: 13,
            scrollBeyondLastLine: false,
            automaticLayout: true,
            renderOverviewRuler: false,
            diffWordWrap: 'off',
          }}
        />
      )}
    </div>
  )
}

export default DiffView
