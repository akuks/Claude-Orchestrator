import { describe, expect, it } from 'vitest'
import { checkDecision, decisionNoteLine, displayId, makeRuleDraft } from './decisions'

const d = (touches, over = {}) => ({ number: 12, display_id: 'D-012', title: 'Use Stripe', touches, ...over })

describe('makeRuleDraft', () => {
  it('uses the first touches entry without wildcards', () => {
    expect(makeRuleDraft(d(['src/**/x.js', 'src/pay.js']))).toEqual({ kind: 'never', match: 'path', pattern: 'src/pay.js', reason: 'D-012 Use Stripe' })
  })
  it('drops a trailing /** or /*', () => {
    expect(makeRuleDraft(d(['src/billing/**'])).pattern).toBe('src/billing')
    expect(makeRuleDraft(d(['src/billing/*'])).pattern).toBe('src/billing')
  })
  it('is empty when every entry has a wildcard or there are none', () => {
    expect(makeRuleDraft(d(['**/*.js', 'a?c'])).pattern).toBe('')
    expect(makeRuleDraft(d([])).pattern).toBe('')
  })
})

describe('helpers', () => {
  it('formats ids and the run-log line', () => {
    expect(displayId(7)).toBe('D-007')
    expect(displayId(1234)).toBe('D-1234')
    expect(decisionNoteLine({ ids: [12, 15], path: 'src/a.js' })).toBe('Showed D-012, D-015 before editing src/a.js')
  })
  it('validates', () => {
    expect(checkDecision({ kind: 'decision', title: ' ' })).toMatch(/title/)
    expect(checkDecision({ kind: 'reference', title: 't', see: '' })).toMatch(/needs a see/)
    expect(checkDecision({ kind: 'reference', title: 't', see: 'doc.md' })).toBeNull()
  })
})
