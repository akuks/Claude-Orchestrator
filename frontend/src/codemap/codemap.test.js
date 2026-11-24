import { describe, expect, it } from 'vitest'
import { presetDraft, statusLine } from './codemap'

const base = { enabled: true, server_registered: true, status: 'ok', sha: 'abc1234', behind: 0, error: null }

describe('statusLine', () => {
  it('maps every state to plain words', () => {
    expect(statusLine({ ...base, enabled: false })).toBe('')
    expect(statusLine({ ...base, server_registered: false })).toBe('Add the Code map tool first')
    expect(statusLine({ ...base, status: 'never', sha: null })).toBe('Not built yet')
    expect(statusLine({ ...base, status: 'running', sha: null })).toBe('Building…')
    expect(statusLine(base)).toBe('Up to date')
    expect(statusLine({ ...base, behind: 1 })).toBe('1 change behind — refreshing soon')
    expect(statusLine({ ...base, behind: 3 })).toBe('3 changes behind — refreshing soon')
    expect(statusLine({ ...base, status: 'failed', error: 'timed out' })).toBe('Last refresh failed — timed out')
  })
  it('turns a preset into form values', () => {
    expect(presetDraft({ name: 'Code map', command: 'npx', args: ['-y', 'x@1'] })).toMatchObject({ name: 'Code map', command: 'npx', args: '-y x@1' })
  })
})
