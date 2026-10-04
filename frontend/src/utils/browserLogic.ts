import type { ToolId } from '../components/editor/toolMeta'

// 内置浏览器与产物区面板的可测纯逻辑：从组件里抽出，node 环境直接断言。

// 标题栏面板开关决策：收起态展开时，有最近工具标签则聚焦它，
// 没有则只展开面板、保持「打开标签页」选择页（不强行激活任何标签）
export type PanelToggleDecision =
  | { action: 'collapse' }
  | { action: 'expand'; focusTool: ToolId | null }

export function panelToggleDecision(
  expanded: boolean,
  lastToolId: ToolId | null,
): PanelToggleDecision {
  if (expanded) return { action: 'collapse' }
  return { action: 'expand', focusTool: lastToolId }
}

// 地址栏输入归一化：无协议时，含点号且无空格按网址补 https，否则视为搜索词
export function normalizeAddress(raw: string): string {
  const target = raw.trim()
  if (!target) return ''
  if (/^[a-zA-Z][a-zA-Z0-9+.-]*:/.test(target)) return target
  if (/\.[a-zA-Z]/.test(target) && !target.includes(' ')) return `https://${target}`
  return `https://www.bing.com/search?q=${encodeURIComponent(target)}`
}
