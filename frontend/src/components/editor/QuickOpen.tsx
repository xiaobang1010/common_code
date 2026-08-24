import { useCallback, useEffect, useRef, useState } from 'react'

// 快速打开：Ctrl+P 文件名模糊匹配工作区文件，回车在编辑区打开

interface QuickOpenProps {
  open: boolean
  onClose: () => void
  onOpenFile: (path: string) => void
}

interface FileEntry {
  name: string
  path: string
  dir: string
}

// 取工作区全部文件（复用递归列目录接口，扁平化成列表）
async function fetchAllFiles(): Promise<FileEntry[]> {
  try {
    const res = await fetch('/api/files/list?path=.&recursive=true')
    const data = await res.json()
    const files: FileEntry[] = []
    const walk = (items: { name: string; type: string; path: string; children?: unknown[] }[]) => {
      for (const it of items) {
        if (it.type === 'dir') {
          walk((it.children || []) as { name: string; type: string; path: string; children?: unknown[] }[])
        } else {
          const segs = it.path.split('/')
          files.push({ name: it.name, path: it.path, dir: segs.slice(0, -1).join('/') })
        }
      }
    }
    walk((data.items || []) as { name: string; type: string; path: string; children?: unknown[] }[])
    return files
  } catch {
    return []
  }
}

// 匹配排序：名称前缀命中优先，其次包含命中；同优先级按路径排序
function rankAndFilter(files: FileEntry[], q: string): FileEntry[] {
  const lower = q.toLowerCase()
  const prefix: FileEntry[] = []
  const contains: FileEntry[] = []
  for (const f of files) {
    const idx = f.name.toLowerCase().indexOf(lower)
    if (idx === 0) prefix.push(f)
    else if (idx > 0) contains.push(f)
  }
  return [...prefix, ...contains].slice(0, 50)
}

function QuickOpen({ open, onClose, onOpenFile }: QuickOpenProps) {
  const [query, setQuery] = useState('')
  const [allFiles, setAllFiles] = useState<FileEntry[]>([])
  const [cursor, setCursor] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)

  // 打开时拉取文件清单并聚焦输入框
  useEffect(() => {
    if (!open) return
    setQuery('')
    setCursor(0)
    void fetchAllFiles().then(setAllFiles)
    setTimeout(() => inputRef.current?.focus(), 0)
  }, [open])

  const results = query.trim() ? rankAndFilter(allFiles, query.trim()) : []

  const pick = useCallback(
    (path: string) => {
      onOpenFile(path)
      onClose()
    },
    [onOpenFile, onClose]
  )

  if (!open) return null

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') {
      e.preventDefault()
      onClose()
    } else if (e.key === 'ArrowDown') {
      e.preventDefault()
      setCursor((c) => Math.min(c + 1, results.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setCursor((c) => Math.max(c - 1, 0))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      const target = results[cursor]
      if (target) pick(target.path)
    }
  }

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 200,
        backgroundColor: 'var(--scrim)',
        display: 'flex',
        justifyContent: 'center',
        alignItems: 'flex-start',
        paddingTop: '12vh',
      }}
      onClick={onClose}
    >
      <div
        style={{
          width: '480px',
          maxWidth: '80vw',
          backgroundColor: 'var(--bg-elevated)',
          border: '1px solid var(--border)',
          borderRadius: 'var(--radius-md)',
          boxShadow: 'var(--shadow-lg)',
          overflow: 'hidden',
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <input
          ref={inputRef}
          value={query}
          onChange={(e) => {
            setQuery(e.target.value)
            setCursor(0)
          }}
          onKeyDown={handleKeyDown}
          placeholder="输入文件名快速打开 (Ctrl+P)"
          style={{
            width: '100%',
            boxSizing: 'border-box',
            border: 'none',
            borderBottom: '1px solid var(--border)',
            background: 'transparent',
            color: 'var(--text-primary)',
            fontSize: '13px',
            fontFamily: 'var(--font-ui)',
            padding: '12px 14px',
            outline: 'none',
          }}
        />
        <div style={{ maxHeight: '320px', overflow: 'auto', padding: '6px 0' }}>
          {results.length === 0 && query.trim() !== '' && (
            <div style={{ padding: '10px 14px', color: 'var(--text-tertiary)', fontSize: '12px', fontFamily: 'var(--font-ui)' }}>
              没有匹配的文件
            </div>
          )}
          {results.map((f, i) => (
            <div
              key={f.path}
              onClick={() => pick(f.path)}
              onMouseEnter={() => setCursor(i)}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '8px',
                padding: '7px 14px',
                cursor: 'pointer',
                backgroundColor: i === cursor ? 'var(--selected-bg)' : 'transparent',
                fontFamily: 'var(--font-ui)',
              }}
            >
              <span style={{ fontSize: '12px', color: 'var(--text-primary)', fontWeight: 500 }}>
                {f.name}
              </span>
              <span
                style={{
                  fontSize: '11px',
                  color: 'var(--text-tertiary)',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                }}
              >
                {f.dir}
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

export default QuickOpen
