import { describe, expect, it } from 'vitest'
import { costToday, greetingFor, displayName, parseTime, selectNeedsYou, selectRecent } from './useHomeData'

const NOW = new Date('2026-11-20T12:00:00Z').getTime()
const me = { id: 'u1', name: 'Asha', email: 'asha@x.io' }
const hoursAgo = (h) => new Date(NOW - h * 3600_000).toISOString()

describe('selectNeedsYou', () => {
  const base = { approvals: [], failedTasks: [], schedules: [], creds: { claude: 'own' }, me, now: NOW }

  it('is empty when nothing needs the viewer', () => {
    expect(selectNeedsYou(base)).toEqual([])
    expect(selectNeedsYou({ me, now: NOW })).toEqual([])
  })

  it('includes every approval (already filtered server-side)', () => {
    const items = selectNeedsYou({
      ...base,
      approvals: [
        { id: 'a1', title: 'Deploy', created_by: 'u2', created_at: hoursAgo(1), risk: 'critical' },
        { id: 'a2', title: 'Migrate', created_by: 'u1', created_at: hoursAgo(50) },
      ],
    })
    expect(items.map((i) => [i.kind, i.id])).toEqual([
      ['approval', 'a1'],
      ['approval', 'a2'],
    ])
    expect(items[0].title).toBe('Deploy')
  })

  it('includes pending action approvals after task approvals', () => {
    const items = selectNeedsYou({
      ...base,
      approvals: [{ id: 'a1', title: 'Deploy', created_at: hoursAgo(1) }],
      actionApprovals: [
        { id: 'r1', task_id: 't1', task_title: 'Clean', tool: 'Bash', summary: 'git reset --hard\nmore', rule_ids: ['git-reset-hard'], created_at: hoursAgo(0.1), can_decide: true },
      ],
    })
    expect(items.map((i) => [i.kind, i.id])).toEqual([
      ['approval', 'a1'],
      ['action', 'r1'],
    ])
    expect(items[1].title).toBe('Bash: git reset --hard')
    expect(items[1].detail).toContain('Clean')
    expect(items[1].detail).toContain('Discard uncommitted work')
    expect(items[1].fullTitle).toBe('Bash: git reset --hard\nmore')
  })

  it('keeps only own failed tasks within the last 24h', () => {
    const items = selectNeedsYou({
      ...base,
      failedTasks: [
        { id: 'f1', title: 'mine recent', created_by: 'u1', status: 'failed', created_at: hoursAgo(30), completed_at: hoursAgo(2) },
        { id: 'f2', title: 'mine old', created_by: 'u1', status: 'failed', created_at: hoursAgo(40), completed_at: hoursAgo(25) },
        { id: 'f3', title: 'not mine', created_by: 'u2', status: 'failed', created_at: hoursAgo(1), completed_at: hoursAgo(1) },
        { id: 'f4', title: 'mine, no completed_at', created_by: 'u1', status: 'failed', created_at: hoursAgo(3), completed_at: null },
        { id: 'f5', title: 'mine, created 23h ago', created_by: 'u1', status: 'failed', created_at: hoursAgo(23) },
      ],
    })
    expect(items.filter((i) => i.kind === 'failed').map((i) => i.id)).toEqual(['f1', 'f4', 'f5'])
  })

  it('lists own paused schedules only', () => {
    const items = selectNeedsYou({
      ...base,
      schedules: [
        { id: 's1', name: 'Nightly', created_by: 'u1', blocked_reason: 'owner lost access to project' },
        { id: 's2', name: 'Fine', created_by: 'u1', blocked_reason: null },
        { id: 's3', name: 'Theirs', created_by: 'u2', blocked_reason: 'owner is disabled' },
        { id: 's4', name: 'Ownerless', created_by: null, blocked_reason: 'schedule has no owner' },
      ],
    })
    const s = items.filter((i) => i.kind === 'schedule')
    expect(s.map((i) => i.id)).toEqual(['s1'])
    expect(s[0].title).toBe('Nightly')
    expect(s[0].detail).toMatch(/lost access/)
  })

  it('adds the missing-credential item first when claude is none', () => {
    const items = selectNeedsYou({
      ...base,
      creds: { claude: 'none' },
      approvals: [{ id: 'a1', title: 'Deploy', created_at: hoursAgo(1) }],
    })
    expect(items[0].kind).toBe('credential')
    expect(items.map((i) => i.kind)).toEqual(['credential', 'approval'])
    expect(selectNeedsYou({ ...base, creds: { claude: 'fallback' } })).toEqual([])
    expect(selectNeedsYou({ ...base, creds: null })).toEqual([])
  })

  it('gives every item a unique key', () => {
    const items = selectNeedsYou({
      ...base,
      creds: { claude: 'none' },
      approvals: [{ id: 'x', title: 'a' }],
      failedTasks: [{ id: 'x', title: 'b', created_by: 'u1', created_at: hoursAgo(1) }],
      schedules: [{ id: 'x', name: 'c', created_by: 'u1', blocked_reason: 'r' }],
    })
    expect(new Set(items.map((i) => i.key)).size).toBe(items.length)
  })
})

describe('selectRecent', () => {
  it('returns own finished threads, newest finish first, max 5', () => {
    const tasks = [
      { id: 't1', created_by: 'u1', status: 'completed', completed_at: hoursAgo(5) },
      { id: 't2', created_by: 'u1', status: 'running', completed_at: null },
      { id: 't3', created_by: 'u2', status: 'completed', completed_at: hoursAgo(1) },
      { id: 't4', created_by: 'u1', status: 'failed', completed_at: hoursAgo(1) },
      { id: 't5', created_by: 'u1', status: 'cancelled', completed_at: hoursAgo(2) },
      { id: 't6', created_by: 'u1', status: 'completed', completed_at: hoursAgo(3) },
      { id: 't7', created_by: 'u1', status: 'completed', completed_at: hoursAgo(4) },
      { id: 't8', created_by: 'u1', status: 'completed', completed_at: hoursAgo(9) },
      { id: 't9', created_by: 'u1', status: 'awaiting_approval' },
    ]
    expect(selectRecent(tasks, me).map((t) => t.id)).toEqual(['t4', 't5', 't6', 't7', 't1'])
    expect(selectRecent(undefined, me)).toEqual([])
  })
})

describe('small helpers', () => {
  it('parseTime reads offset-less backend timestamps as UTC', () => {
    expect(parseTime('2026-11-20T11:00:00')).toBe(Date.UTC(2026, 10, 20, 11))
    expect(parseTime('2026-11-20T11:00:00.123456')).toBe(Date.UTC(2026, 10, 20, 11, 0, 0, 123))
    expect(parseTime('2026-11-20T11:00:00+00:00')).toBe(Date.UTC(2026, 10, 20, 11))
    expect(parseTime('2026-11-20T11:00:00Z')).toBe(Date.UTC(2026, 10, 20, 11))
    expect(parseTime(null)).toBe(null)
    expect(parseTime('nope')).toBe(null)
  })
  it('the 24h window uses UTC for offset-less timestamps', () => {
    const now = Date.UTC(2026, 10, 21, 10)
    const failed = [{ id: 'f', title: 'x', created_by: 'u1', completed_at: '2026-11-20T09:30:00' }]
    expect(selectNeedsYou({ failedTasks: failed, me, now })).toEqual([])
    expect(selectNeedsYou({ failedTasks: failed, me, now: Date.UTC(2026, 10, 21, 9) })).toHaveLength(1)
  })
  it('costToday reads usage summary', () => {
    expect(costToday({ today: { cost_usd: 4.18 } })).toBe(4.18)
    expect(costToday(null)).toBe(null)
  })
  it('displayName prefers name, then the email local part', () => {
    expect(displayName({ name: 'Asha K', email: 'a@x' })).toBe('Asha K')
    expect(displayName({ name: '', email: 'ashutosh@x.io' })).toBe('ashutosh')
    expect(displayName(null)).toBe('there')
  })
  it('greetingFor picks by local hour', () => {
    expect(greetingFor(new Date(2026, 10, 20, 8))).toBe('Good morning')
    expect(greetingFor(new Date(2026, 10, 20, 14))).toBe('Good afternoon')
    expect(greetingFor(new Date(2026, 10, 20, 20))).toBe('Good evening')
    expect(greetingFor(new Date(2026, 10, 20, 2))).toBe('Good evening')
  })
})
