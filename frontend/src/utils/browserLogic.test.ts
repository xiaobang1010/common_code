import { describe, expect, it } from 'vitest'
import { normalizeAddress, panelToggleDecision } from './browserLogic'
import { pickerActionFor } from '../components/editor/TabPicker'

describe('pickerActionFor：选择页卡片点击路由', () => {
  it('终端卡片走终端面板开关', () => {
    expect(pickerActionFor('terminal')).toEqual({ kind: 'terminal' })
  })

  it('审查/浏览器卡片走对应工具标签', () => {
    expect(pickerActionFor('review')).toEqual({ kind: 'tool', toolId: 'review' })
    expect(pickerActionFor('browser')).toEqual({ kind: 'tool', toolId: 'browser' })
  })

  it('未知卡片不产生动作', () => {
    expect(pickerActionFor('summary')).toBeNull()
    expect(pickerActionFor('search')).toBeNull()
  })
})

describe('panelToggleDecision：标题栏面板开关', () => {
  it('展开态点击即收起', () => {
    expect(panelToggleDecision(true, 'review')).toEqual({ action: 'collapse' })
  })

  it('收起态展开并聚焦最近工具标签', () => {
    expect(panelToggleDecision(false, 'browser')).toEqual({ action: 'expand', focusTool: 'browser' })
  })

  it('无最近工具标签时只展开、保持选择页', () => {
    expect(panelToggleDecision(false, null)).toEqual({ action: 'expand', focusTool: null })
  })
})

describe('normalizeAddress：地址栏输入归一化', () => {
  it('带协议原样通过', () => {
    expect(normalizeAddress('https://example.com')).toBe('https://example.com')
    expect(normalizeAddress(' http://localhost:5173 ')).toBe('http://localhost:5173')
  })

  it('含点号无空格按网址补 https', () => {
    expect(normalizeAddress('example.com')).toBe('https://example.com')
    expect(normalizeAddress('example.com/path?q=1')).toBe('https://example.com/path?q=1')
  })

  it('其余输入走搜索', () => {
    expect(normalizeAddress('rust 异步教程')).toContain('/search?q=')
    expect(normalizeAddress('rust 异步教程')).toContain(encodeURIComponent('rust 异步教程'))
  })

  it('空输入返回空串', () => {
    expect(normalizeAddress('   ')).toBe('')
  })
})
