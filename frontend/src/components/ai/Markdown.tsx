import { memo } from 'react'
import { Streamdown } from 'streamdown'
import { code } from '@streamdown/code'
import { math } from '@streamdown/math'
import { cjk } from '@streamdown/cjk'
import { createMermaidPlugin } from '@streamdown/mermaid'

// 插件对象模块级单例：Streamdown 内部对 plugins 做引用相等比较，
// 在组件里重建会让已渲染块的记忆化失效，流式更新越来越卡
const plugins = {
  code,
  math,
  cjk,
  // mermaid 图表插件必须放进 plugins：streamdown 引擎只从 plugins.mermaid
  // 查找 diagram 插件，误放顶层 mermaid prop 会静默退化为普通代码块且 tsc 不报错；
  // 应用深色-only，图表配置同步用 dark 主题
  mermaid: createMermaidPlugin({ config: { theme: 'dark' } }),
}

interface Props {
  // 待渲染的 markdown 文本（流式增量累积后的完整内容）
  content: string
  // true 走流式模式：streamdown 自动修复未闭合语法（remend）并显示跟随末行的光标
  streaming?: boolean
}

// AI 回复正文与 .md 文件预览的统一渲染入口。
// streamdown 按块记忆化渲染：流式期间只有末块重算，长回复不随内容变长变卡
function Markdown({ content, streaming = false }: Props) {
  return (
    <Streamdown
      mode={streaming ? 'streaming' : 'static'}
      plugins={plugins}
      // caret 无默认值且仅在 isAnimating 为 true 时渲染，两个 prop 必须成对显式传
      caret="block"
      isAnimating={streaming}
      // 关闭内置的链接确认弹层：外链保持真实 <a>（target=_blank + rel 加固），
      // 点击由 Electron 主进程 setWindowOpenHandler 统一转交系统浏览器
      linkSafety={{ enabled: false }}
    >
      {content}
    </Streamdown>
  )
}

export default memo(Markdown)
