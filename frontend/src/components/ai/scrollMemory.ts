// 会话滚动位置记忆：内存级 Map，记录离开每条会话时的阅读锚点，供切回时恢复。
// 不存裸 scrollTop：工作块启用 content-visibility 后，屏外块的高度是估算值，
// 内容替换完成时刻的总高度并不准，按数值恢复会偏。
// 存「锚块 + 块内偏移」：恢复时把锚块重新对齐到视口，块级位置与离开时一致。
// 纯内存不持久化：应用重启后 Map 为空，所有会话按无记忆处理（进入默认到底部）。

// 锚点两种形态：
// 'bottom' = 离开时停在底部，切回直接置底；
// 对象 = 离开时停在中间，blockId 是视口顶部第一个可见的工作块，
// offsetPx 是锚块顶到滚动容器顶的像素距离（可为负，表示已滚进锚块内部），
// blockIndex 是锚块在 blockIds 里的下标，作 blockId 找不到时的兜底
// （直播块切回后经历史重建会换成稳定 id，按 id 定位失败时按下标找回位置）
export type ScrollAnchor =
  | 'bottom'
  | { blockId: string; blockIndex: number; offsetPx: number }

// 会话 id → 锚点。条目极小（一个短对象），按访问过的会话数增长，无需淘汰；
// 会话删除后遗留的条目不可达（id 唯一不复用），无害残留，不做清理
const memory = new Map<string, ScrollAnchor>()

export function saveAnchor(sessionId: string, anchor: ScrollAnchor) {
  memory.set(sessionId, anchor)
}

// 无记忆返回 undefined，调用方按置底处理
export function readAnchor(sessionId: string): ScrollAnchor | undefined {
  return memory.get(sessionId)
}
