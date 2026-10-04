import { describe, expect, it } from 'vitest'
import { computeChatContentWidth } from './chatContentWidth'

// 浮点档位乘积允许 0.01px 误差
const close = (actual: number, expected: number) => expect(actual).toBeCloseTo(expected, 2)

describe('computeChatContentWidth 分档边界', () => {
  it('窄面板由下限兜底', () => {
    close(computeChatContentWidth(800), 880)
    close(computeChatContentWidth(1200), 880)
    // 1201 进入 65% 档但 780.65 不足下限，仍兜 880——钉死与参考实现的「无倒挂」差异
    close(computeChatContentWidth(1201), 880)
  })

  it('65% 档', () => {
    close(computeChatContentWidth(1400), 910)
    close(computeChatContentWidth(1600), 1040)
  })

  it('60% 档，跨档向下跳变与参考实现一致', () => {
    close(computeChatContentWidth(1601), 960.6)
    close(computeChatContentWidth(1720), 1032)
    close(computeChatContentWidth(2000), 1200)
  })

  it('55% 档与 1400 封顶', () => {
    close(computeChatContentWidth(2001), 1100.55)
    close(computeChatContentWidth(2600), 1400)
  })

  it('非法输入退回下限', () => {
    close(computeChatContentWidth(0), 880)
    close(computeChatContentWidth(-100), 880)
    close(computeChatContentWidth(NaN), 880)
  })
})
