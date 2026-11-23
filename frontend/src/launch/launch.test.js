import { describe, expect, it } from 'vitest'
import { criticalPathsLine, isOpen, nudgeText, questionLaunchLine, rowActions, titleFromPrompt } from './launch'

describe('launch helpers', () => {
  it('nudge copy is exact', () => {
    expect(nudgeText(4, 7)).toBe('This looks like new scope: the launch list has 4 open items; this would become #7.')
  })
  it('title is the first non-empty line, at most 200 characters', () => {
    expect(titleFromPrompt('\n  hello  \nmore')).toBe('hello')
    expect(titleFromPrompt('x'.repeat(300))).toHaveLength(200)
    expect(titleFromPrompt('')).toBe('')
  })
  it('open means open or started', () => {
    expect(['open', 'started', 'done', 'dropped'].map((status) => isOpen({ status }))).toEqual([true, true, false, false])
  })
  it('contributors get no Reopen, Edit, Move or Delete', () => {
    expect(rowActions({ status: 'open' }, false)).toEqual(['start', 'done', 'drop'])
    expect(rowActions({ status: 'done' }, false)).toEqual(['drop'])
    expect(rowActions({ status: 'done' }, true)).toEqual(['drop', 'reopen', 'edit', 'move', 'delete'])
  })
  it('chip and question copy', () => {
    expect(criticalPathsLine({ paths: ['a', 'b'] })).toBe('This run changed 2 critical path(s): a, b')
    expect(questionLaunchLine({ number: 2, title: 'T' })).toBe('blocks launch item #2: T')
    expect(questionLaunchLine(null)).toBeNull()
  })
})
