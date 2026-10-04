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

// 多模态 content 拆解结果：text 为拼接后的纯文本，images 为图片块（按出现序）
export interface ExtractedContent {
  text: string
  images: Array<{ name: string; mime: string; dataUrl: string }>
}

// 从消息 content（字符串或 OpenAI parts 数组）提取文本与图片附件。
// /api/state 轮询形态下 dataUrl 为 `data:<mime>;base64,__omitted__` 占位，
// 是否回落本地缓存由调用方决定
export function extractContentParts(content: unknown): ExtractedContent {
  if (typeof content === 'string') return { text: content, images: [] }
  if (Array.isArray(content)) {
    const texts: string[] = []
    const images: ExtractedContent['images'] = []
    for (const block of content) {
      if (!block || typeof block !== 'object') continue
      const b = block as Record<string, unknown>
      if (b.type === 'text' && typeof b.text === 'string') {
        texts.push(b.text)
      } else if (b.type === 'image_url') {
        const url = (b.image_url as { url?: unknown } | undefined)?.url
        if (typeof url === 'string' && url.startsWith('data:')) {
          const mime = url.slice(5, url.indexOf(';')) || 'image/png'
          images.push({ name: '', mime, dataUrl: url })
        }
      }
    }
    return { text: texts.join('\n'), images }
  }
  return { text: '', images: [] }
}

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
  // 后台子代理的任务通知是给模型看的机器消息，展示为对话块标题会泄漏内部机制
  if (content.startsWith('<task-notification>')) {
    return { kind: 'skip', text: '' }
  }
  return { kind: 'plain', text: content }
}
