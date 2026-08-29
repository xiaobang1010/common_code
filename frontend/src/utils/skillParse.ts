// 技能触发消息的历史解析纯函数 — 供 useChatStore 与 scripts/verify_skill_parse.mjs 共用。
// 零第三方依赖：node v24 可直接 import .ts。

// 解析结果三类：skill=技能触发（徽章+任务描述）、skip=系统注入消息（不建块）、plain=普通用户消息
export interface UserMessageParseResult {
  kind: 'skill' | 'skip' | 'plain'
  skillName?: string
  text: string
}

// 渐进披露的重写提示形状（/api/command 技能命中时前端发来的 prompt）。
// 严格整形匹配：形状不完整的普通消息（如用户粘贴讨论）不会误吞。
const SKILL_REWRITE_RE =
  /^Use the skill named `([^`\n]+)` for this turn\.\n[\s\S]*?\nUser request:[ \t]*([\s\S]*)$/

// 解析一条 user 消息在历史加载时的展示形态：
// 1. 新格式重写提示 → skill（徽章 + User request 段任务文本，空任务为空串）
// 2. system-reminder 开头的其余消息 → skip 不建块。覆盖两类：Skill 工具
//    注入的正文（防幽灵块）、旧格式内联消息（不做存量兼容，一律跳过）
// 3. 其余 → plain 原样展示
export function parseUserMessage(content: string): UserMessageParseResult {
  const m = SKILL_REWRITE_RE.exec(content)
  if (m) {
    return { kind: 'skill', skillName: m[1], text: m[2].trim() }
  }
  if (content.startsWith('<system-reminder>')) {
    return { kind: 'skip', text: '' }
  }
  return { kind: 'plain', text: content }
}
