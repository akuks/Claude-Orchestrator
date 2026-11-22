import { describe, expect, it } from 'vitest'
import { canContinue, notices, stepLabel } from './resume'

const rec = (over = {}) => ({
  type: 'recovery',
  payload: { why: 'the server restarted', at: '2026-11-22T10:00:00+00:00', in_flight: 2, orphan_killed: true, recoveries: 1, gave_up: false, ...over },
})
const kinds = (l) => l.map((n) => n.kind)

describe('notices', () => {
  it('recovered, from the recovery event', () => {
    const l = notices({}, [rec()], null)
    expect(kinds(l)).toEqual(['recovered'])
    expect(l[0].text).toContain('the server restarted')
    expect(l[0].text).toContain('2 actions were in flight')
  })
  it('gave_up', () => {
    const l = notices({}, [rec({ gave_up: true, recoveries: 2 })], null)
    expect(kinds(l)).toEqual(['gave_up'])
    expect(l[0].text).toContain('2')
  })
  it('credentials_changed only when the fresh_session flag is true', () => {
    expect(kinds(notices({}, [{ type: 'fresh_session', payload: { reason: 'x', credentials_changed: true } }], null))).toEqual(['credentials_changed'])
    expect(notices({}, [{ type: 'fresh_session', payload: { reason: 'x' } }], null)).toEqual([])
  })
  it('handoff-derived kinds', () => {
    const h = { content: { outside_commits: [{ sha: 'abc', subject: 's' }], no_commits: true, outside_docs: ['/tmp/a.md'] } }
    expect(kinds(notices({}, [], h))).toEqual(['outside_commits', 'no_commits', 'temp_files'])
    expect(kinds(notices({}, [], { content: { history_rewritten: true, outside_commits: [{ sha: 'a', subject: 's' }] } }))).toEqual(['history_rewritten'])
  })
  it('nothing for no events and no handoff', () => {
    expect(notices(null, undefined, null)).toEqual([])
  })
})

describe('stepLabel', () => {
  it('formats, or null', () => {
    expect(stepLabel({ n: 2, m: 4, text: 'wire it up' })).toBe('Step 2 of 4: wire it up')
    expect(stepLabel({ n: null, m: null, text: 'x' })).toBeNull()
    expect(stepLabel(null)).toBeNull()
  })
  it('shows hidden characters', () => {
    expect(stepLabel({ n: 1, m: 2, text: 'a‮b' })).toContain('⟦U+202E⟧')
  })
})

describe('canContinue', () => {
  const task = { id: 't', status: 'failed', origin: 'user', created_by: 'u1', session_id: 's1' }
  const owner = { id: 'u1' }
  it('owner may resume and go fresh', () => {
    expect(canContinue(task, owner, [task])).toEqual({ resume: true, fresh: true })
  })
  it('non-owner may only go fresh', () => {
    expect(canContinue(task, { id: 'u2' }, [task])).toEqual({ resume: false, fresh: true })
  })
  it('no session: fresh only', () => {
    expect(canContinue({ ...task, session_id: null }, owner, [])).toEqual({ resume: false, fresh: true })
  })
  it('an active task, or a sibling that is active, allows neither', () => {
    const none = { resume: false, fresh: false }
    expect(canContinue({ ...task, status: 'running' }, owner, [])).toEqual(none)
    expect(canContinue(task, owner, [task, { id: 'x', status: 'queued' }])).toEqual(none)
  })
  it('an agent step allows neither', () => {
    expect(canContinue({ ...task, origin: 'agent' }, owner, [])).toEqual({ resume: false, fresh: false })
  })
  it('completed is terminal and allowed here (the button hides it)', () => {
    expect(canContinue({ ...task, status: 'completed' }, owner, []).resume).toBe(true)
  })
  it('no user: neither', () => {
    expect(canContinue(task, null, [])).toEqual({ resume: false, fresh: false })
  })
})
