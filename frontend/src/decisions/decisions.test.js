import { describe, expect, it } from 'vitest'
import { checkDecision, decisionNoteLine, decisionReviewLine, displayId, entryWords, longEntryHint, makeRuleDraft, proposalOrigin } from './decisions'

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

describe('phase 4b helpers', () => {
  it('counts words of title + why', () => {
    expect(entryWords('a b', 'c d e')).toBe(5)
    expect(entryWords('a', '')).toBe(1)
  })
  const words = (n) => Array.from({ length: n }, (_, i) => `w${i}`).join(' ')
  it('long entry hint only above 80 words', () => {
    expect(longEntryHint(words(40), words(40))).toBeNull()
    expect(longEntryHint(words(40), words(41))).toBe('81 words — shorter entries are read more reliably')
  })
  it('origin per source', () => {
    expect(proposalOrigin({ source: 'run', task_title: 'T' })).toBe('from run T')
    expect(proposalOrigin({ source: 'answer' })).toBe('from an answer')
    expect(proposalOrigin({ source: 'agent' })).toBe('suggested by an agent')
    expect(proposalOrigin({ source: 'lesson', thread_title: 'Th' })).toBe('lesson from Th')
    expect(proposalOrigin({ source: 'person', proposed_by_name: 'Ann' })).toBe('suggested by Ann')
  })
  it('decision review line', () => {
    expect(decisionReviewLine({ proposed: 2, stale: [4] })).toBe('Suggested 2 decisions for review · flagged D-004 as maybe out of date')
    expect(decisionReviewLine({ proposed: 1, stale: [] })).toBe('Suggested 1 decision for review')
    expect(decisionReviewLine({ proposed: 0, stale: [4, 7] })).toBe('flagged D-004, D-007 as maybe out of date')
  })
})
