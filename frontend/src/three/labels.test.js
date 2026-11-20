import { describe, expect, it } from 'vitest'
import { stackLabels } from './labels'

describe('stackLabels', () => {
  it('keeps labels at their anchors when they do not overlap', () => {
    expect(stackLabels([{ y: 50, h: 20 }, { y: 100, h: 20 }], 300)).toEqual([50, 100])
  })

  it('pushes overlapping labels apart, keeping their order by anchor', () => {
    const out = stackLabels([{ y: 100, h: 30 }, { y: 90, h: 30 }, { y: 95, h: 30 }], 400, 4)
    // anchor order: 90, 95, 100 → stacked 34px apart starting from the first
    expect(out[1]).toBe(90)
    expect(out[2]).toBe(124)
    expect(out[0]).toBe(158)
  })

  it('keeps the stack inside the box (shifts up from the bottom edge)', () => {
    const out = stackLabels([{ y: 280, h: 30 }, { y: 290, h: 30 }], 300, 4)
    expect(Math.max(...out) + 15).toBeLessThanOrEqual(300)
    expect(Math.abs(out[1] - out[0])).toBeGreaterThanOrEqual(34)
  })

  it('handles an empty list', () => {
    expect(stackLabels([], 100)).toEqual([])
  })
})

describe('placeLabel', () => {
  it('marks the first placement (so a CSS fallback position stops applying) and skips sub-pixel moves', async () => {
    const { placeLabel } = await import('./labels')
    const el = document.createElement('div')
    expect(el.hasAttribute('data-placed')).toBe(false)
    placeLabel(el, { x: 10, y: 20 }, 'under')
    expect(el.hasAttribute('data-placed')).toBe(true)
    expect(el.style.visibility).toBe('visible')
    const t = el.style.transform
    expect(t).toContain('translate3d(10px, 20px, 0)')
    placeLabel(el, { x: 10.1, y: 20.1 }, 'under')
    expect(el.style.transform).toBe(t)
  })
})
