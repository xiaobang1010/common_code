import { memo, useMemo } from 'react'
import { Streamdown, defaultRemarkPlugins } from 'streamdown'
import type { PluggableList } from 'unified'
import { code } from '@streamdown/code'
import { createMathPlugin } from '@streamdown/math'
import { cjk } from '@streamdown/cjk'
import { createMermaidPlugin } from '@streamdown/mermaid'

// 数学插件显式开启单 $ 行内公式：默认配置只认 $$ 块级，讲义类文档里的
// 「求 $\lim...$」会原样显示成 LaTeX 源码；解析失败兜底沿用插件默认 errorColor
const math = createMathPlugin({ singleDollarTextMath: true })

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
  // md 文件所在目录（工作区相对路径，可为空串表示根目录）：提供时相对图片路径
  // 解析为原始字节接口 URL；对话场景不传，图片 URL 原样透传
  basePath?: string
}

// 带协议前缀的 URL（http:、data: 等）不参与相对路径解析
const SCHEME_RE = /^[a-zA-Z][a-zA-Z\d+.-]*:/

// 归并 ./ 与 ../ 段：路径拼进查询参数后浏览器不再做相对解析，需自己收敛
function normalizeRelPath(p: string): string {
  const out: string[] = []
  for (const seg of p.split('/')) {
    if (seg === '' || seg === '.') continue
    if (seg === '..') {
      out.pop()
      continue
    }
    out.push(seg)
  }
  return out.join('/')
}

// 相对图片路径 → 原始字节接口 URL：前导 / 按「工作区根相对」处理，其余按
// 「md 所在目录相对」拼接；markdown 源里的路径可能已带百分号编码，先解码
// 还原真实文件名再整体编码为 path 参数，中文/空格才能正确命中
function resolveImageSrc(url: string, basePath: string): string {
  const clean = url.split('#')[0].split('?')[0]
  let decoded = clean
  try {
    decoded = decodeURIComponent(clean)
  } catch {
    // 非法编码序列按原文处理
  }
  const joined = decoded.startsWith('/')
    ? normalizeRelPath(decoded.slice(1))
    : normalizeRelPath(`${basePath}/${decoded}`)
  return `/api/files/raw?path=${encodeURIComponent(joined)}`
}

// remark 插件：在 mdast 阶段改写 image 节点的 url。必须赶在 HTML 加固之前——
// 加固会把相对 URL 按页面根归一（./dir/x.svg 变 /dir/x.svg），md 所在目录信息
// 就此丢失；只碰 image 节点，链接（link 节点）不受影响；
// 引用式图片（![a][ref]）与内联 HTML <img> 不在改写范围
function remarkLocalImages(basePath: string) {
  return (tree: unknown) => {
    const walk = (node: Record<string, unknown> | null | undefined) => {
      if (!node || typeof node !== 'object') return
      if (node.type === 'image' && typeof node.url === 'string') {
        const url = node.url
        if (!SCHEME_RE.test(url) && !url.startsWith('//') && !url.startsWith('#')) {
          node.url = resolveImageSrc(url, basePath)
        }
      }
      const children = node.children
      if (Array.isArray(children)) {
        for (const child of children) walk(child as Record<string, unknown>)
      }
    }
    walk(tree as Record<string, unknown>)
  }
}

// AI 回复正文与 .md 文件预览的统一渲染入口。
// streamdown 按块记忆化渲染：流式期间只有末块重算，长回复不随内容变长变卡
function Markdown({ content, streaming = false, basePath }: Props) {
  // 仅在文件预览（传了 basePath）时追加图片路径解析插件；对话场景保持默认管线。
  // 数组按 basePath 记忆化，避免每次渲染重建插件链打断块级记忆化
  // 插件以 [工厂, 参数] 元组传入：unified 会把数组项当插件调用并拿回 transformer，
  // 直接传已执行的 transformer 会在挂载阶段被空调一次而静默失效（实测踩过）
  const remarkPlugins = useMemo<PluggableList | undefined>(
    () =>
      basePath === undefined
        ? undefined
        : [...Object.values(defaultRemarkPlugins), [remarkLocalImages, basePath]],
    [basePath]
  )
  return (
    // md-body 是排版覆盖的作用域钩子：streamdown 默认样式偏通用文档风
    //（标题过大、代码块双层框、表格带底色表头），对话区需要更紧凑克制的排印，
    // 具体覆盖见 index.css 的 .md-body 规则块
    <div className="md-body">
      <Streamdown
        mode={streaming ? 'streaming' : 'static'}
        plugins={plugins}
        // caret 无默认值且仅在 isAnimating 为 true 时渲染，两个 prop 必须成对显式传
        caret="block"
        isAnimating={streaming}
        // 关闭内置的链接确认弹层：外链保持真实 <a>（target=_blank + rel 加固），
        // 点击由 Electron 主进程 setWindowOpenHandler 统一转交系统浏览器
        linkSafety={{ enabled: false }}
        // 代码块不显示行号：聊天场景下行号是噪音，保持无行号的平铺代码卡
        lineNumbers={false}
        remarkPlugins={remarkPlugins}
      >
        {content}
      </Streamdown>
    </div>
  )
}

export default memo(Markdown)
