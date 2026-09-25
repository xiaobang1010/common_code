// 对话内容列宽的分档计算：面板越宽、可读列适当加宽但始终居中封顶，
// 避免「窄列两侧大片留白」与「超宽行长不可读」两个极端。
// 档位比例与业界主流客户端同源；下限取本项目既有基准 880（参考实现为 832），
// 保证窄面板不比现状回退，也消除参考实现中小宽度处「面板变宽、列反而变窄」的倒挂。

const MIN_WIDTH = 880
const MAX_WIDTH = 1400

/** 按面板宽度返回内容列最大宽度（px）。分档参数集中在此处，调整只改这一处。 */
export function computeChatContentWidth(panelWidth: number): number {
  // 非法宽度（未测量/竞态 0 值）退回下限，由列容器 width:100% 自然退化
  if (!Number.isFinite(panelWidth) || panelWidth <= 0) return MIN_WIDTH
  let pct: number
  if (panelWidth <= 1600) pct = 0.65
  else if (panelWidth <= 2000) pct = 0.6
  else pct = 0.55
  return Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, panelWidth * pct))
}
