import { describe, expect, it } from 'vitest'
import { answerableCount, openQuestions, questionView, reversibleChip } from './questions'

const q = (over = {}) => ({
  id: 'q1',
  status: 'open',
  can_answer: true,
  question: 'Migrate the old table?',
  options: ['yes', 'no'],
  recommendation: 'yes',
  blocks: 'the release',
  reversible: true,
  source: 'agent',
  ...over,
})

describe('questions helpers', () => {
  it('counts only open questions the viewer may answer', () => {
    expect(answerableCount([q(), q({ can_answer: false }), q({ status: 'answered' }), null])).toBe(1)
    expect(answerableCount(undefined)).toBe(0)
  })
  it('keeps only open questions', () => {
    expect(openQuestions([q(), q({ id: 'q2', status: 'dismissed' })]).map((x) => x.id)).toEqual(['q1'])
  })
  it('marks the recommended option', () => {
    const v = questionView(q())
    expect(v.options.map((o) => o.recommended)).toEqual([true, false])
    expect(v.recInOptions).toBe(true)
  })
  it('reveals hidden characters in every text and counts them', () => {
    const v = questionView(q({ question: 'ok‮?', options: ['a​'], recommendation: 'r', blocks: 'b⁦' }))
    expect(v.question).toContain('⟦U+202E⟧')
    expect(v.options[0].text).toContain('⟦U+200B⟧')
    expect(v.blocks).toContain('⟦U+2066⟧')
    expect(v.hidden).toBe(3)
    expect(v.recInOptions).toBe(false)
  })
  it('reversible chip: yes, no, unknown', () => {
    expect(reversibleChip(true).text).toBe('reversible')
    expect(reversibleChip(false)).toEqual({ text: 'not reversible', warn: true })
    expect(reversibleChip(null).text).toBe('reversible: unknown')
  })
})
