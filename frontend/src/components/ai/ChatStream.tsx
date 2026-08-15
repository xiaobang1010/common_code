import { useRef, useEffect, useState, memo, useCallback } from 'react'
import WorkBlockView from './WorkBlock'
import { useChatStore } from '../../stores/useChatStore'

// 距底部小于该值视为"贴底"，继续自动跟随
const NEAR_BOTTOM_PX = 120

interface Props {
  // 是否已打开工作区：无工作区时显示引导空态
  hasWorkspace: boolean
  onOpenWorkspace: () => void
}

function ChatStream({ hasWorkspace, onOpenWorkspace }: Props) {
  // 只订阅 id 列表：新增/删除块时更新，单块内容变化不触发本组件重渲
  const blockIds = useChatStore(s => s.blockIds)
  const containerRef = useRef<HTMLDivElement>(null)
  const sentinelRef = useRef<HTMLDivElement>(null)
  // 用户是否在底部附近：在才自动滚动跟随，上滚查看历史时停止抢占
  const [stickToBottom, setStickToBottom] = useState(true)

  // 用 IntersectionObserver 观察底部哨兵：可见视为贴底
  useEffect(() => {
    const el = containerRef.current
    const sentinel = sentinelRef.current
    if (!el || !sentinel) return
    const observer = new IntersectionObserver(
      (entries) => {
        setStickToBottom(entries[0]?.isIntersecting ?? false)
      },
      { root: el, rootMargin: `0px 0px ${NEAR_BOTTOM_PX}px 0px` }
    )
    observer.observe(sentinel)
    return () => observer.disconnect()
  }, [])

  // 内容变化且用户贴底时，下一帧跟随到底部（auto，不用 smooth 避免动画反复重启）
  useEffect(() => {
    if (!stickToBottom) return
    const el = containerRef.current
    if (!el) return
    const raf = requestAnimationFrame(() => {
      el.scrollTo({ top: el.scrollHeight, behavior: 'auto' })
    })
    return () => cancelAnimationFrame(raf)
  }, [blockIds, stickToBottom])

  // 回到底部：平滑滚动后由 observer 自动恢复跟随
  const scrollToBottomSmooth = useCallback(() => {
    const el = containerRef.current
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' })
  }, [])

  return (
    <div style={{ position: 'relative', flex: 1, minHeight: 0, display: 'flex' }}>
      <div
        ref={containerRef}
        style={{
          flex: 1,
          overflowY: 'auto',
          display: 'flex',
          flexDirection: 'column',
          gap: '20px',
          padding: '20px 24px',
        }}
      >
        {blockIds.length === 0 ? (
          hasWorkspace ? (
          <div
            style={{
              flex: 1,
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              justifyContent: 'center',
              gap: '16px',
              color: 'var(--text-tertiary)',
            }}
          >
            <div
              style={{
                width: '56px',
                height: '56px',
                borderRadius: '50%',
                background: 'var(--bg-elevated)',
                border: '1px solid var(--border-strong)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                boxShadow: '0 8px 24px rgba(0, 0, 0, 0.35)',
                marginBottom: '4px',
              }}
            >
              <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="var(--text-primary)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 2a3 3 0 0 0-3 3v1H7a3 3 0 0 0-3 3v1H3a1 1 0 0 0 0 2h1v1a3 3 0 0 0 3 3h.5a3 3 0 0 0 3 3h1.5a3 3 0 0 0 3-3h.5a3 3 0 0 0 3-3v-1h1a1 1 0 0 0 0-2h-1V9a3 3 0 0 0-3-3h-2V5a3 3 0 0 0-3-3z" />
              </svg>
            </div>
            <div
              style={{
                fontSize: '15px',
                fontWeight: 500,
                color: 'var(--text-secondary)',
                letterSpacing: '0.3px',
              }}
            >
              开始与 AI 对话
            </div>
            <div
              style={{
                fontSize: '12px',
                color: 'var(--text-tertiary)',
                fontFamily: 'var(--font-mono)',
                maxWidth: '320px',
                textAlign: 'center',
                lineHeight: 1.6,
              }}
            >
              描述你想做什么，AI 会读代码、改文件、跑命令
            </div>
          </div>
          ) : (
          <div
            style={{
              flex: 1,
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              justifyContent: 'center',
              gap: '16px',
              color: 'var(--text-tertiary)',
            }}
          >
            <div
              style={{
                width: '56px',
                height: '56px',
                borderRadius: '50%',
                background: 'var(--bg-elevated)',
                border: '1px solid var(--border-strong)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                boxShadow: '0 8px 24px rgba(0, 0, 0, 0.35)',
                marginBottom: '4px',
              }}
            >
              <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="var(--text-primary)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z" />
                <path d="M9 13h6" />
              </svg>
            </div>
            <div
              style={{
                fontSize: '15px',
                fontWeight: 500,
                color: 'var(--text-secondary)',
                letterSpacing: '0.3px',
              }}
            >
              打开一个工作区开始使用
            </div>
            <div
              style={{
                fontSize: '12px',
                color: 'var(--text-tertiary)',
                fontFamily: 'var(--font-mono)',
                maxWidth: '320px',
                textAlign: 'center',
                lineHeight: 1.6,
              }}
            >
              AI 会在你选定的项目里读代码、改文件、跑命令
            </div>
            <button
              onClick={onOpenWorkspace}
              style={{
                padding: '9px 20px',
                border: '1px solid var(--border-strong)',
                borderRadius: 'var(--radius-md)',
                backgroundColor: 'var(--button-primary-bg)',
                color: 'var(--button-primary-text)',
                fontSize: '13px',
                fontFamily: 'var(--font-ui)',
                fontWeight: 500,
                cursor: 'pointer',
                transition: 'all var(--transition-fast)',
                display: 'flex',
                alignItems: 'center',
                gap: '6px',
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.backgroundColor = 'var(--button-primary-bg-hover)'
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.backgroundColor = 'var(--button-primary-bg)'
              }}
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z" />
                <path d="M9 13h6" />
              </svg>
              打开工作区
            </button>
          </div>
          )
        ) : (
          blockIds.map(id => (
            <WorkBlockView key={id} blockId={id} />
          ))
        )}
        {/* 底部哨兵：可见表示用户贴底 */}
        <div ref={sentinelRef} style={{ height: '1px', flexShrink: 0 }} />
      </div>

      {/* 用户上滚查看历史时显示回到底部按钮 */}
      {!stickToBottom && blockIds.length > 0 && (
        <button
          onClick={scrollToBottomSmooth}
          title="回到底部"
          style={{
            position: 'absolute',
            bottom: '16px',
            right: '28px',
            display: 'flex',
            alignItems: 'center',
            gap: '6px',
            padding: '6px 12px',
            fontSize: '12px',
            color: 'var(--text-primary)',
            backgroundColor: 'var(--bg-elevated)',
            border: '1px solid var(--border-strong)',
            borderRadius: 'var(--radius-md)',
            cursor: 'pointer',
            boxShadow: 'var(--shadow-md)',
            zIndex: 10,
          }}
        >
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M12 5v14M5 12l7 7 7-7" />
          </svg>
          回到底部
        </button>
      )}
    </div>
  )
}

export default memo(ChatStream)
