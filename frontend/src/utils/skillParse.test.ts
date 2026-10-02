import { describe, expect, it } from 'vitest'
import { extractContentParts, parseUserMessage } from './skillParse'

describe('extractContentParts 多模态拆解', () => {
  it('字符串 content 原样返回文本', () => {
    expect(extractContentParts('你好')).toEqual({ text: '你好', images: [] })
  })

  it('parts 数组拼接 text 块并按序提取图片', () => {
    const parts = [
      { type: 'text', text: '看这张图' },
      { type: 'image_url', image_url: { url: 'data:image/png;base64,AAA' } },
      { type: 'image_url', image_url: { url: 'data:image/jpeg;base64,BBB' } },
    ]
    const out = extractContentParts(parts)
    expect(out.text).toBe('看这张图')
    expect(out.images).toEqual([
      { name: '', mime: 'image/png', dataUrl: 'data:image/png;base64,AAA' },
      { name: '', mime: 'image/jpeg', dataUrl: 'data:image/jpeg;base64,BBB' },
    ])
  })

  it('纯图片无 text 块：文本为空串、图片完整', () => {
    const out = extractContentParts([
      { type: 'image_url', image_url: { url: 'data:image/png;base64,__omitted__' } },
    ])
    expect(out.text).toBe('')
    expect(out.images[0].dataUrl).toContain('__omitted__')
  })

  it('非字符串 content 的其他形态安全兜底', () => {
    expect(extractContentParts(123)).toEqual({ text: '', images: [] })
    expect(extractContentParts(null)).toEqual({ text: '', images: [] })
    expect(extractContentParts([{ type: 'unknown' }])).toEqual({ text: '', images: [] })
  })

  it('多个 text 块以换行拼接', () => {
    const out = extractContentParts([
      { type: 'text', text: '第一段' },
      { type: 'text', text: '第二段' },
    ])
    expect(out.text).toBe('第一段\n第二段')
  })
})

describe('parseUserMessage 既有形状回归', () => {
  it('空串（纯图片消息提取后）为 plain', () => {
    expect(parseUserMessage('')).toEqual({ kind: 'plain', text: '' })
  })

  it('system-reminder 与 task-notification 仍为 skip', () => {
    expect(parseUserMessage('<system-reminder>x').kind).toBe('skip')
    expect(parseUserMessage('<task-notification>x').kind).toBe('skip')
  })
})
