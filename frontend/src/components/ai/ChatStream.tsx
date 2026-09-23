import { useRef, useEffect, useState, memo, useCallback } from 'react'
import WorkBlockView from './WorkBlock'
import { useChatStore } from '../../stores/useChatStore'
import { saveAnchor, readAnchor, type ScrollAnchor } from './scrollMemory'

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
  // 运行态订阅：浮动条在流式期间附带停止入口（停止唯一常驻入口仍在输入区）
  const isStreaming = useChatStore(s => s.isStreaming)
  const abort = useChatStore(s => s.abort)
  // 会话 id：位置记忆的时序锚点，翻转即视为一次会话切换
  const sessionId = useChatStore(s => s.sessionId)
  const containerRef = useRef<HTMLDivElement>(null)
  const sentinelRef = useRef<HTMLDivElement>(null)
  // 用户是否在底部附近：在才自动滚动跟随，上滚查看历史时停止抢占
  const [stickToBottom, setStickToBottom] = useState(true)

  // ---- 会话滚动位置记忆 ----
  // 切走时采集旧会话阅读锚点，切回时恢复。可行性依赖一个事实：App 各切换
  // 入口里 setSessionId 与 loadMessages 之间隔着 await，id 翻转的这次渲染里
  // DOM 与 blockIds 都还是旧会话的内容，此刻读一次即可
  const prevSessionIdRef = useRef<string | null>(null)
  // 恢复意图：id 翻转时记下要给新会话恢复什么，等新内容到达再消费；
  // anchor 为 null 表示无记忆，消费时置底
  const pendingRestoreRef = useRef<{ sessionId: string; anchor: ScrollAnchor | null } | null>(null)
  // 内容快照：上一提交与 id 翻转时刻的 blockIds。前者用于识别「翻转同批内容
  // 被清空」（新建会话形态），后者作为消费侧的到达证明——内容引用变了才算
  // 新会话数据真的落地，防止翻转时的旧内容/空态被误当成到达
  const prevBlockIdsRef = useRef<string[]>([])
  const currBlockIdsRef = useRef<string[]>([])
  const flipBlockIdsRef = useRef<string[] | null>(null)

  // 提交级内容快照：声明在采集 effect 之前，保证同一提交里先于采集执行
  useEffect(() => {
    prevBlockIdsRef.current = currBlockIdsRef.current
    currBlockIdsRef.current = blockIds
  }, [blockIds])

  // 采集：给被离开的会话记锚点。前提——旧 id 非 null（仅跳过采集）；翻转时
  // 内容仍在且归属旧会话（首块 id 前缀校验，快速连切时 DOM 可能还挂着更早
  // 会话的内容，宁可不记也不能记串）。贴底记 'bottom'，否则记视口顶部
  // 第一个可见块（底边越过容器顶即算可见）
  useEffect(() => {
    const prev = prevSessionIdRef.current
    prevSessionIdRef.current = sessionId
    if (prev === sessionId) return
    if (prev && blockIds.length > 0 && blockIds[0].startsWith(`${prev}:`)) {
      const el = containerRef.current
      if (el) {
        if (stickToBottom) {
          saveAnchor(prev, 'bottom')
        } else {
          const containerTop = el.getBoundingClientRect().top
          let captured = false
          for (const node of el.querySelectorAll<HTMLElement>('[data-block-id]')) {
            const rect = node.getBoundingClientRect()
            if (rect.bottom >= containerTop) {
              const id = node.dataset.blockId || ''
              saveAnchor(prev, {
                blockId: id,
                blockIndex: blockIds.indexOf(id),
                offsetPx: rect.top - containerTop,
              })
              captured = true
              break
            }
          }
          // 无可见块的极端滚动位置兜底按置底
          if (!captured) saveAnchor(prev, 'bottom')
        }
      }
    }
    // 给新会话备恢复意图。但「翻转同批内容被清空」（当前空且上一提交非空，
    // 即新建会话形态）且新会话无记忆时不备：这种会话不会再有历史加载到达，
    // 意图悬空会在日后的轮询重建时误消费，把正在阅读的视图抢占到底部
    if (!sessionId) {
      pendingRestoreRef.current = null
    } else {
      const remembered = readAnchor(sessionId)
      const contentVanished = blockIds.length === 0 && prevBlockIdsRef.current.length > 0
      if (remembered || !contentVanished) {
        pendingRestoreRef.current = { sessionId, anchor: remembered ?? null }
      }
    }
    // 记下翻转时刻的内容引用，供消费侧做到达证明
    flipBlockIdsRef.current = blockIds
    // 依赖只留 sessionId：采集只在 id 翻转时刻做一次快照，
    // blockIds/stickToBottom 取当次渲染的值即可
  }, [sessionId]) // eslint-disable-line react-hooks/exhaustive-deps

  // 消费：新会话内容到达后按意图恢复。意图会话必须与当前一致；到达以内容
  // 引用变化为证（与翻转时刻不同）；非空内容还要过归属校验（首块 id 前缀）。
  // 归属不符——上一会话滞后的快照晚到——则本次跳过、意图保留，等正确内容
  // 到达再消费，不产生错位恢复
  useEffect(() => {
    const intent = pendingRestoreRef.current
    if (!intent || intent.sessionId !== sessionId) return
    if (blockIds === flipBlockIdsRef.current) return
    if (blockIds.length === 0) {
      // 空内容到达：无可恢复，按置底收场
      pendingRestoreRef.current = null
      setStickToBottom(true)
      return
    }
    if (!blockIds[0].startsWith(`${sessionId}:`)) return
    pendingRestoreRef.current = null
    const el = containerRef.current
    if (!el) return
    const anchor = intent.anchor
    if (!anchor || anchor === 'bottom') {
      setStickToBottom(true)
      return
    }
    // 锚点恢复：先关掉贴底跟随，避免恢复期间的内容增长把视图拽走
    setStickToBottom(false)
    // 按 id 定位锚块；未命中（直播块切回后重建换了稳定 id）回退记忆的下标，
    // 下标也越界则放弃恢复、按置底处理
    const query = (id: string) =>
      el.querySelector<HTMLElement>(`[data-block-id="${CSS.escape(id)}"]`)
    const anchorEl =
      query(anchor.blockId) ??
      (anchor.blockIndex >= 0 && anchor.blockIndex < blockIds.length
        ? query(blockIds[anchor.blockIndex])
        : null)
    if (!anchorEl) {
      setStickToBottom(true)
      return
    }
    // 短程漂移校正：content-visibility 的估算高度、字体与代码高亮的异步
    // 加载、工作块入场动画，都会让首次放置后的位置漂移。rAF 循环把「锚块
    // 顶到容器顶的距离」拉回记忆的 offsetPx，偏差超 1px 就补偿；连续稳定
    // 或超出时间预算（覆盖 280ms 入场动画）即止。补偿被钳制（内容不够高
    // 滚不到目标位置）时直接放弃，避免空转
    let raf = 0
    let stableFrames = 0
    const start = performance.now()
    const tick = () => {
      // 内容已被再次替换（用户又切走了）时立即退出
      if (!anchorEl.isConnected) return
      const delta =
        anchorEl.getBoundingClientRect().top - el.getBoundingClientRect().top - anchor.offsetPx
      if (Math.abs(delta) > 1) {
        const before = el.scrollTop
        el.scrollTop += delta
        if (el.scrollTop === before) return
        stableFrames = 0
      } else {
        stableFrames += 1
        if (stableFrames >= 3) return
      }
      if (performance.now() - start < 450) raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [blockIds, sessionId])

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
      {/* 滚动容器保持全宽：滚动条贴 AI 面板右缘，不随内容列缩进 */}
      <div
        ref={containerRef}
        style={{
          flex: 1,
          overflowY: 'auto',
        }}
      >
        {/* 限宽可读列：消息与空态共用；minHeight 撑满滚动容器保证空态垂直居中，
            窄屏（面板不足 880px）时 max-width 自动退化为全宽、仅保留左右留白 */}
        <div
          style={{
            maxWidth: 'var(--content-max-width)',
            width: '100%',
            margin: '0 auto',
            boxSizing: 'border-box',
            minHeight: '100%',
            display: 'flex',
            flexDirection: 'column',
            gap: '20px',
            padding: '20px var(--content-pad-x)',
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
                boxShadow: 'var(--shadow-lg)',
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
                boxShadow: 'var(--shadow-lg)',
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
        {/* 底部哨兵：可见表示用户贴底（随消息列包裹在内容列内） */}
        <div ref={sentinelRef} style={{ height: '1px', flexShrink: 0 }} />
        </div>
      </div>

      {/* 用户上滚查看历史时显示回到底部按钮；流式期间并列停止入口，保证离底时控制仍可触达 */}
      {!stickToBottom && blockIds.length > 0 && (
        <div
          style={{
            position: 'absolute',
            bottom: '16px',
            right: '28px',
            display: 'flex',
            alignItems: 'center',
            gap: '8px',
            zIndex: 10,
          }}
        >
          <button
            onClick={scrollToBottomSmooth}
            title="回到底部"
            style={{
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
            }}
          >
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <path d="M12 5v14M5 12l7 7 7-7" />
            </svg>
            回到底部
          </button>
          {isStreaming && (
            <button
              onClick={abort}
              title="停止生成"
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '4px',
                padding: '6px 12px',
                fontSize: '12px',
                border: '1px solid var(--error)',
                borderRadius: 'var(--radius-md)',
                background: 'var(--bg-elevated)',
                color: 'var(--error)',
                cursor: 'pointer',
                boxShadow: 'var(--shadow-md)',
                transition: 'all var(--transition-fast)',
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.background = 'var(--error)'
                e.currentTarget.style.color = 'var(--button-primary-text)'
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.background = 'var(--bg-elevated)'
                e.currentTarget.style.color = 'var(--error)'
              }}
            >
              停止
              <svg width="9" height="9" viewBox="0 0 24 24" fill="currentColor">
                <rect x="6" y="6" width="12" height="12" rx="1.5" />
              </svg>
            </button>
          )}
        </div>
      )}
    </div>
  )
}

export default memo(ChatStream)
