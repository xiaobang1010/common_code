import { useState } from 'react'

// 搜索接口返回的单条结果
interface SearchResult {
  path: string
  line_number: number
  line: string
  matches: { start: number; end: number }[]
}

interface SearchPanelProps {
  onFileOpen: (path: string) => void
}

function SearchPanel({ onFileOpen }: SearchPanelProps) {
  const [query, setQuery] = useState('')
  const [caseSensitive, setCaseSensitive] = useState(false)
  const [regex, setRegex] = useState(false)
  const [results, setResults] = useState<SearchResult[]>([])
  const [loading, setLoading] = useState(false)
  const [searched, setSearched] = useState(false) // 是否已执行过搜索
  const [error, setError] = useState('')

  // 执行搜索
  const handleSearch = async () => {
    const q = query.trim()
    if (!q) {
      setResults([])
      setSearched(true)
      return
    }
    setLoading(true)
    setError('')
    try {
      const params = new URLSearchParams({
        q,
        case_sensitive: String(caseSensitive),
        regex: String(regex),
      })
      const res = await fetch(`/api/search?${params.toString()}`)
      const json = await res.json()
      if (json.error) {
        setError(json.error)
        setResults([])
      } else {
        setResults(json.results || [])
      }
    } catch (e) {
      setError('搜索失败')
      console.error(e)
    } finally {
      setSearched(true)
      setLoading(false)
    }
  }

  // 按文件路径分组
  const groups = new Map<string, SearchResult[]>()
  for (const r of results) {
    const arr = groups.get(r.path) || []
    arr.push(r)
    groups.set(r.path, arr)
  }

  // 渲染一行内容，匹配部分用高亮背景标记
  const renderLine = (result: SearchResult) => {
    const { line, matches } = result
    if (matches.length === 0) {
      return <span>{line}</span>
    }
    // 按 start 排序后分段拼接，未匹配部分正常显示，匹配部分高亮
    const sorted = [...matches].sort((a, b) => a.start - b.start)
    const parts: React.ReactNode[] = []
    let cursor = 0
    sorted.forEach((m, i) => {
      if (m.start > cursor) {
        parts.push(<span key={`t-${i}`}>{line.slice(cursor, m.start)}</span>)
      }
      parts.push(
        <span
          key={`m-${i}`}
          style={{
            backgroundColor: 'rgba(255, 193, 7, 0.35)',
            color: 'var(--text-primary)',
          }}
        >
          {line.slice(m.start, m.end)}
        </span>
      )
      cursor = Math.max(cursor, m.end)
    })
    if (cursor < line.length) {
      parts.push(<span key="t-tail">{line.slice(cursor)}</span>)
    }
    return <>{parts}</>
  }

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      {/* 搜索输入区 */}
      <div
        style={{
          padding: '8px',
          flexShrink: 0,
          borderBottom: '1px solid var(--border)',
          display: 'flex',
          flexDirection: 'column',
          gap: '6px',
        }}
      >
        <div style={{ display: 'flex', gap: '4px' }}>
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                handleSearch()
              }
            }}
            placeholder="搜索"
            style={{
              flex: 1,
              backgroundColor: 'var(--bg-primary)',
              border: '1px solid var(--border)',
              color: 'var(--text-primary)',
              padding: '4px 8px',
              fontSize: '13px',
              outline: 'none',
              borderRadius: '2px',
            }}
          />
          {/* 区分大小写开关 */}
          <button
            onClick={() => setCaseSensitive(!caseSensitive)}
            title="区分大小写"
            style={{
              border: `1px solid ${caseSensitive ? 'var(--accent)' : 'var(--border)'}`,
              backgroundColor: caseSensitive ? 'var(--accent)' : 'var(--bg-primary)',
              color: caseSensitive ? '#fff' : 'var(--text-secondary)',
              width: '26px',
              cursor: 'pointer',
              fontSize: '12px',
              fontWeight: 'bold',
              borderRadius: '2px',
            }}
          >
            Aa
          </button>
          {/* 正则表达式开关 */}
          <button
            onClick={() => setRegex(!regex)}
            title="正则表达式"
            style={{
              border: `1px solid ${regex ? 'var(--accent)' : 'var(--border)'}`,
              backgroundColor: regex ? 'var(--accent)' : 'var(--bg-primary)',
              color: regex ? '#fff' : 'var(--text-secondary)',
              width: '26px',
              cursor: 'pointer',
              fontSize: '12px',
              fontWeight: 'bold',
              borderRadius: '2px',
            }}
          >
            .*
          </button>
        </div>
        <button
          onClick={handleSearch}
          disabled={loading}
          style={{
            width: '100%',
            border: '1px solid var(--border)',
            backgroundColor: 'var(--bg-primary)',
            color: 'var(--text-primary)',
            padding: '4px 8px',
            fontSize: '13px',
            cursor: loading ? 'not-allowed' : 'pointer',
            borderRadius: '2px',
          }}
        >
          {loading ? '搜索中...' : '搜索'}
        </button>
      </div>

      {/* 结果区 */}
      <div style={{ flex: 1, overflow: 'auto' }}>
        {loading ? (
          <div
            style={{ padding: '8px 12px', color: 'var(--text-secondary)', fontSize: '13px' }}
          >
            搜索中...
          </div>
        ) : error ? (
          <div style={{ padding: '8px 12px', color: 'var(--error)', fontSize: '13px' }}>
            {error}
          </div>
        ) : results.length === 0 ? (
          <div
            style={{ padding: '8px 12px', color: 'var(--text-secondary)', fontSize: '13px' }}
          >
            {searched ? '没有找到结果' : '输入关键词开始搜索'}
          </div>
        ) : (
          Array.from(groups.entries()).map(([path, items]) => (
            <div key={path}>
              {/* 文件分组标题 */}
              <div
                title={path}
                style={{
                  padding: '4px 12px',
                  color: 'var(--text-primary)',
                  fontSize: '13px',
                  backgroundColor: 'var(--bg-primary)',
                  whiteSpace: 'nowrap',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  position: 'sticky',
                  top: 0,
                }}
              >
                {path}
                <span style={{ color: 'var(--text-secondary)', marginLeft: '6px' }}>
                  {items.length}
                </span>
              </div>
              {/* 匹配行 */}
              {items.map((r) => (
                <div
                  key={`${r.path}:${r.line_number}`}
                  onClick={() => onFileOpen(r.path)}
                  title={r.line}
                  style={{
                    display: 'flex',
                    gap: '8px',
                    padding: '2px 12px',
                    fontSize: '13px',
                    cursor: 'pointer',
                    whiteSpace: 'nowrap',
                    overflow: 'hidden',
                    color: 'var(--text-primary)',
                  }}
                >
                  <span style={{ color: 'var(--text-secondary)', flexShrink: 0 }}>
                    {r.line_number}
                  </span>
                  <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>
                    {renderLine(r)}
                  </span>
                </div>
              ))}
            </div>
          ))
        )}
      </div>
    </div>
  )
}

export default SearchPanel
