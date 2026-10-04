// 技能消息历史解析的验证脚本 — 与 useChatStore 共用 skillParse.ts 唯一实现。
// 用法：node scripts/verify_skill_parse.mjs（在 frontend/ 或项目根均可，node v24 直接跑 .ts）

import { parseUserMessage } from '../frontend/src/utils/skillParse.ts'

const rewriteWithTask = [
  'Use the skill named `spec` for this turn.',
  'First call the `Skill` tool with skill="spec" before doing the task.',
  'After the skill content is loaded, follow its instructions and continue.',
  '',
  'User request: 分析这个项目',
].join('\n')

const rewriteEmpty = [
  'Use the skill named `spec` for this turn.',
  'First call the `Skill` tool with skill="spec" before doing the task.',
  'After the skill content is loaded, follow its instructions and continue.',
  '',
  'User request is empty: ask the user what they want to do first; do NOT invent a task.',
  'User request: ',
].join('\n')

const legacyWithTask = [
  '<system-reminder>',
  '按 spec 驱动开发模式工作：先对齐再执行，文档即进度。',
  '',
  '## 用户任务',
  '分析这个项目',
  '',
  '</system-reminder>',
].join('\n')

const legacyEnforcementOnly = [
  '<system-reminder>',
  '技能正文……',
  '',
  '## 执行要求',
  '本消息就是 spec 工作流的触发指令……',
  '</system-reminder>',
].join('\n')

const skillInjectedBody = [
  '<system-reminder>',
  '按 spec 驱动开发模式工作：先对齐再执行，文档即进度。',
  '（无 ## 用户任务 / ## 执行要求 标记段——Skill 工具 new_messages 的形态）',
  '</system-reminder>',
].join('\n')

const pseudoRewrite = 'Use the skill named `spec` 我随便说点啥，形状不完整'
const plainMessage = '修一下登录页的样式'

const cases = [
  ['新格式带任务', rewriteWithTask, { kind: 'skill', skillName: 'spec', text: '分析这个项目' }],
  ['新格式空 args', rewriteEmpty, { kind: 'skill', skillName: 'spec', text: '' }],
  ['Skill 注入正文', skillInjectedBody, { kind: 'skip', text: '' }],
  ['旧格式含用户任务', legacyWithTask, { kind: 'skip', text: '' }],
  ['旧格式仅执行要求', legacyEnforcementOnly, { kind: 'skip', text: '' }],
  ['伪新格式形状不完整', pseudoRewrite, { kind: 'plain', text: pseudoRewrite }],
  ['普通消息', plainMessage, { kind: 'plain', text: plainMessage }],
]

let failed = 0
for (const [name, input, expected] of cases) {
  const got = parseUserMessage(input)
  const ok = got.kind === expected.kind && got.skillName === expected.skillName && got.text === expected.text
  if (!ok) {
    failed++
    console.log(`FAIL ${name}\n  got:      ${JSON.stringify(got)}\n  expected: ${JSON.stringify(expected)}`)
  } else {
    console.log(`PASS ${name}`)
  }
}

if (failed > 0) {
  console.log(`\n${failed} FAILED`)
  process.exit(1)
}
console.log('\nALL PASS')
