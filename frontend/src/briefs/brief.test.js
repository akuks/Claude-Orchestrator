import { describe, expect, it } from 'vitest'
import { BRIEF_KEYS, briefMissing, briefPayload, briefScopeLine } from './brief'

describe('brief helpers', () => {
  it('sends no brief when every field is empty', () => {
    expect(briefPayload(undefined)).toBeUndefined()
    expect(briefPayload({})).toBeUndefined()
    expect(briefPayload({ goal: '  ', can_change: [], retries: null, outputs: [] })).toBeUndefined()
    expect(BRIEF_KEYS).toContain('tie_break')
  })
  it('lists the required fields missing once any is filled', () => {
    expect(briefMissing({})).toEqual([])
    expect(briefMissing({ goal: 'x' })).toEqual(['done', 'stop'])
  })
  it('builds the payload, trimming texts and omitting empty optionals', () => {
    const p = briefPayload({
      goal: ' G ', can_change: ['src/**'], cannot_change: [], done: 'D', stop: 'S', setup: '', retries: 2, outputs: [],
    })
    expect(p).toEqual({ goal: 'G', can_change: ['src/**'], cannot_change: [], done: 'D', stop: 'S', retries: 2 })
  })
  it('keeps retries 0', () => {
    expect(briefPayload({ goal: 'g', retries: 0 }).retries).toBe(0)
  })
  it('describes the guard scope', () => {
    expect(briefScopeLine({ shell_roots: 2, file_tools_only: [] })).toBe(
      'Brief: 2 CANNOT folders also guarded for shell commands',
    )
    expect(briefScopeLine({ shell_roots: 1, file_tools_only: ['a', 'b'] })).toBe(
      'Brief: 1 CANNOT folders also guarded for shell commands; file tools only: a, b',
    )
  })
})
