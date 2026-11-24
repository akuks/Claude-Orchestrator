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
    expect(statusLine({ ...base, status: 'ok', sha: null, behind: null })).toBe("Built — this folder has no saved changes history, so freshness can't be checked")
  })
  it('a failed refresh says why in plain words, never the raw reason', () => {
    const failed = (o) => statusLine({ ...base, status: 'failed', ...o })
    const raw = 'The index command exited with code 1: boom at line 3'
    expect(failed({ failure: 'error', error: raw })).toBe('Last refresh failed — the tool stopped with an error')
    expect(failed({ failure: 'timeout', error: 'Timed out after 15 minutes' })).toBe('Last refresh failed — the tool took too long')
    expect(failed({ failure: 'start', error: 'could not start the index command (OSError)' })).toBe('Last refresh failed — the tool could not start')
    expect(failed({ failure: null, error: raw })).toBe('Last refresh failed — the tool stopped with an error')
    expect(failed({ failure: 'error', error: raw })).not.toContain('code 1')
  })
  it('turns a preset into form values', () => {
    expect(presetDraft({ name: 'Code map', command: 'npx', args: ['-y', 'x@1'] })).toMatchObject({ name: 'Code map', command: 'npx', args: '-y x@1' })
  })
})
