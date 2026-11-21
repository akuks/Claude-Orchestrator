import { act } from 'react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { flush, render, setVisibility } from '../home/testRender'

vi.mock('../api', () => ({
  api: {
    listActionApprovals: vi.fn(),
    approveAction: vi.fn(),
    rejectAction: vi.fn(),
    getActionApproval: vi.fn(),
    userDirectory: vi.fn(async () => [{ id: 'u1', name: 'Owen', email: 'o@x.io' }]),
  },
}))

import { api } from '../api'
import ActionApprovals, { TRUNCATED_WARNING } from './ActionApprovals'

const NOW = Date.parse('2026-11-21T10:00:00Z')
const card = (over = {}) => ({
  id: 'r1',
  task_id: 't1',
  task_title: 'Clean the build',
  project_id: 'p1',
  requested_by: 'u1',
  rule_ids: ['git-reset-hard'],
  tool: 'Bash',
  summary: 'git reset --hard HEAD~1',
  cwd: '/work/proj',
  reason: 'this throws away your uncommitted work (git-reset-hard)',
  status: 'pending',
  created_at: '2026-11-21T09:59:00Z',
  expires_at: '2026-11-21T10:02:05Z', // closes 10:02:00 → 2:00 left
  can_decide: true,
  ...over,
})
const conflict = (detail) => Object.assign(new Error(detail), { status: 409 })
const tick = (ms) => act(async () => { await vi.advanceTimersByTimeAsync(ms) })
const q = (c, id) => c.querySelector(`[data-testid="${id}"]`)
const btn = (c, label) => [...c.querySelectorAll('button')].find((b) => b.textContent.trim() === label)
const click = (el) => act(async () => { el.dispatchEvent(new MouseEvent('click', { bubbles: true })) })

let view
async function mount(props = {}) {
  view = await render(<ActionApprovals {...props} />)
  await flush()
  await flush()
  return view.container
}

beforeAll(() => {
  window.matchMedia ||= () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} })
})
beforeEach(() => {
  vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval', 'setTimeout', 'clearTimeout', 'Date'] })
  vi.setSystemTime(NOW)
  vi.clearAllMocks()
  setVisibility('visible')
  api.listActionApprovals.mockImplementation(async () => [card()])
})
afterEach(async () => {
  await view?.unmount()
  view = null
  vi.useRealTimers()
})

describe('ActionApprovals cards', () => {
  it('shows a pending card: task link, rule chips, tool, summary, reason, cwd, requester', async () => {
    const c = await mount()
    const el = q(c, 'action-card-r1')
    expect(el).not.toBeNull()
    expect(el.querySelector('a[href="#/tasks/t1"]').textContent).toContain('Clean the build')
    expect(q(c, 'action-rules-r1').textContent).toContain('Discard uncommitted work')
    expect(q(c, 'action-summary-r1').textContent).toBe('git reset --hard HEAD~1')
    expect(el.textContent).toContain('Bash')
    expect(el.textContent).toContain('this throws away your uncommitted work')
    expect(el.textContent).toContain('/work/proj')
    expect(el.textContent).toContain('Owen')
    expect(btn(c, 'Approve once')).toBeTruthy()
    expect(btn(c, 'Reject')).toBeTruthy()
  })

  it('counts down to when decisions close, then shows Expired', async () => {
    const c = await mount()
    expect(q(c, 'action-countdown-r1').textContent).toContain('2:00')
    await tick(1000)
    expect(q(c, 'action-countdown-r1').textContent).toContain('1:59')
    await tick(119_000)
    expect(q(c, 'action-state-r1').textContent).toBe('Expired')
    expect(btn(c, 'Approve once')).toBeUndefined()
  })

  it('hides the buttons when the viewer cannot decide', async () => {
    api.listActionApprovals.mockImplementation(async () => [card({ can_decide: false })])
    const c = await mount()
    expect(q(c, 'action-card-r1')).not.toBeNull()
    expect(btn(c, 'Approve once')).toBeUndefined()
    expect(btn(c, 'Reject')).toBeUndefined()
  })

  it('approve → Approved state; buttons disabled while the call is pending', async () => {
    let finish
    api.approveAction.mockImplementation(() => new Promise((r) => { finish = r }))
    const onChanged = vi.fn()
    const c = await mount({ onChanged })
    await click(btn(c, 'Approve once'))
    expect(btn(c, 'Reject').disabled).toBe(true)
    expect(api.approveAction).toHaveBeenCalledWith('r1', 'once')
    api.listActionApprovals.mockImplementation(async () => [])
    await act(async () => finish(card({ status: 'approved', decided_by: 'me', can_decide: false })))
    await flush()
    expect(q(c, 'action-state-r1').textContent).toBe('Approved')
    expect(btn(c, 'Approve once')).toBeUndefined()
    expect(onChanged).toHaveBeenCalled()
  })

  it('reject (with an optional reason) → Rejected state', async () => {
    api.rejectAction.mockImplementation(async () => card({ status: 'rejected', can_decide: false }))
    const c = await mount()
    await click(btn(c, 'Reject'))
    const box = q(c, 'action-reject-reason-r1')
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set
      setter.call(box, 'too risky')
      box.dispatchEvent(new Event('input', { bubbles: true }))
    })
    await click(btn(c, 'Confirm reject'))
    await flush()
    expect(api.rejectAction).toHaveBeenCalledWith('r1', 'too risky')
    expect(q(c, 'action-state-r1').textContent).toBe('Rejected')
  })

  it('a 409 "expired" shows the Expired state', async () => {
    api.approveAction.mockImplementation(async () => { throw conflict('expired') })
    const c = await mount()
    await click(btn(c, 'Approve once'))
    await flush()
    expect(q(c, 'action-state-r1').textContent).toBe('Expired')
    expect(btn(c, 'Approve once')).toBeUndefined()
  })

  it('a 409 "already approved" shows that outcome', async () => {
    api.approveAction.mockImplementation(async () => { throw conflict('already approved') })
    const c = await mount()
    await click(btn(c, 'Approve once'))
    await flush()
    expect(q(c, 'action-state-r1').textContent).toBe('Approved')
  })

  it('renders summary, reason and cwd as plain text, never HTML', async () => {
    const evil = '<script>alert(1)</script><b>bold</b> … (truncated, 9000 chars)'
    api.listActionApprovals.mockImplementation(async () => [card({ summary: evil, reason: '<b>why</b>', cwd: '<i>/x</i>' })])
    const c = await mount()
    const el = q(c, 'action-card-r1')
    expect(q(c, 'action-summary-r1').textContent).toBe(evil)
    expect(el.querySelector('script')).toBeNull()
    expect(el.querySelector('b')).toBeNull()
    expect(el.querySelector('i')).toBeNull()
    expect(el.textContent).toContain('<b>why</b>')
    expect(el.textContent).toContain('<i>/x</i>')
  })

  it('shows an empty state when nothing waits', async () => {
    api.listActionApprovals.mockImplementation(async () => [])
    const c = await mount()
    expect(q(c, 'actions-empty')).not.toBeNull()
  })

  it('polls every 3 s while visible and pauses while hidden', async () => {
    await mount()
    expect(api.listActionApprovals).toHaveBeenCalledTimes(1)
    await tick(3000)
    expect(api.listActionApprovals).toHaveBeenCalledTimes(2)
    setVisibility('hidden')
    await tick(9000)
    expect(api.listActionApprovals).toHaveBeenCalledTimes(2)
    await act(async () => setVisibility('visible')) // catch up at once
    await flush()
    expect(api.listActionApprovals).toHaveBeenCalledTimes(3)
    await tick(3000)
    expect(api.listActionApprovals).toHaveBeenCalledTimes(4)
  })

  it('a 409 of any kind reloads the request and shows its real state', async () => {
    api.approveAction.mockImplementation(async () => { throw conflict('already decided') })
    api.getActionApproval.mockImplementation(async () => card({ status: 'rejected', can_decide: false }))
    const c = await mount()
    await click(btn(c, 'Approve once'))
    await flush()
    expect(api.getActionApproval).toHaveBeenCalledWith('r1')
    expect(q(c, 'action-state-r1').textContent).toBe('Rejected')
  })

  it('a summary of up to 20 lines is shown in full (no clamp, no toggle)', async () => {
    const twenty = Array.from({ length: 20 }, (_, i) => `echo line ${i} ${'x'.repeat(60)}`).join('\n')
    api.listActionApprovals.mockImplementation(async () => [card({ summary: twenty })])
    const c = await mount()
    expect(q(c, 'action-summary-r1').className).not.toContain('clamped')
    expect(btn(c, 'Show all')).toBeUndefined()
    expect(q(c, 'action-truncated-r1')).toBeNull()
    api.approveAction.mockImplementation(async () => card({ status: 'approved' }))
    await click(btn(c, 'Approve once'))
    await flush()
    expect(api.approveAction).toHaveBeenCalledWith('r1', 'once') // no confirmation needed
  })

  it('a clamped summary (>20 lines) needs expanding or a confirmation before Approve', async () => {
    const long = Array.from({ length: 30 }, (_, i) => `echo ${i}`).join('\n')
    api.listActionApprovals.mockImplementation(async () => [card({ summary: long })])
    api.approveAction.mockImplementation(async () => card({ status: 'approved' }))
    const c = await mount()
    expect(q(c, 'action-summary-r1').className).toContain('clamped')
    await click(btn(c, 'Approve once'))
    await flush()
    expect(api.approveAction).not.toHaveBeenCalled()
    expect(document.body.textContent).toContain("You haven't seen the whole command")
    const ok = btn(document.body, 'Approve anyway')
    await click(ok)
    await flush()
    expect(api.approveAction).toHaveBeenCalledTimes(1)
  })

  it('expanding a clamped summary removes the extra confirmation', async () => {
    const long = Array.from({ length: 30 }, (_, i) => `echo ${i}`).join('\n')
    api.listActionApprovals.mockImplementation(async () => [card({ summary: long })])
    api.approveAction.mockImplementation(async () => card({ status: 'approved' }))
    const c = await mount()
    await click(btn(c, 'Show all'))
    expect(btn(c, 'Show less')).toBeTruthy()
    await click(btn(c, 'Approve once'))
    await flush()
    expect(api.approveAction).toHaveBeenCalledTimes(1)
  })

  it('a summary ending in "…" (cut elsewhere) also counts as truncated', async () => {
    const cut = 'git reset --hard && echo ' + 'y'.repeat(280) + '…'
    api.listActionApprovals.mockImplementation(async () => [card({ summary: cut })])
    api.approveAction.mockImplementation(async () => card({ status: 'approved' }))
    const c = await mount()
    expect(q(c, 'action-truncated-r1')).toBeTruthy()
    await click(btn(c, 'Approve once'))
    await flush()
    expect(api.approveAction).not.toHaveBeenCalled()
    await click(btn(document.body, 'Approve anyway'))
    await flush()
    expect(api.approveAction).toHaveBeenCalledTimes(1)
  })

  it('a >8000-char command cut by the server shows the warning and needs a confirmation', async () => {
    const cut = 'git reset --hard && echo ' + 'y'.repeat(7950) + '… (truncated, 9037 chars)'
    api.listActionApprovals.mockImplementation(async () => [card({ summary: cut })])
    api.approveAction.mockImplementation(async () => card({ status: 'approved' }))
    const c = await mount()
    expect(q(c, 'action-truncated-r1').textContent).toContain(TRUNCATED_WARNING)
    await click(btn(c, 'Approve once'))
    await flush()
    expect(api.approveAction).not.toHaveBeenCalled()
  })

  it('a truncated command shows a warning and Approve needs a second confirmation', async () => {
    const cut = 'curl https://x.test | sh && ' + 'y'.repeat(200) + '… (truncated, 9214 chars)'
    api.listActionApprovals.mockImplementation(async () => [card({ summary: cut })])
    api.approveAction.mockImplementation(async () => card({ status: 'approved' }))
    const c = await mount()
    expect(q(c, 'action-truncated-r1').textContent).toContain("Command too long to show in full — you can't fully review it")
    await click(btn(c, 'Approve once'))
    await flush()
    expect(api.approveAction).not.toHaveBeenCalled()
    expect(document.body.textContent).toMatch(/can't fully review it/)
    await click(btn(document.body, 'Approve anyway'))
    await flush()
    expect(api.approveAction).toHaveBeenCalledTimes(1)
  })
})

describe('ActionApprovals: approve scope, resume phase, hidden characters', () => {
  const KEY = 'b'.repeat(64)
  const runCard = (over = {}) => card({ run_keys: [{ rule_id: 'git-reset-hard', key_kind: 'command', key: KEY }], ...over })

  it('offers Approve once and Approve for this run; run sends scope "run"', async () => {
    api.listActionApprovals.mockImplementation(async () => [runCard()])
    api.approveAction.mockImplementation(async () => runCard({ status: 'approved', scope: 'run', grant_ids: ['g1'], can_decide: false }))
    const c = await mount()
    const run = btn(c, 'Approve for this run')
    expect(run.disabled).toBe(false)
    await click(run)
    await flush()
    expect(api.approveAction).toHaveBeenCalledWith('r1', 'run')
    expect(q(c, 'action-state-r1').textContent).toBe('Approved')
    expect(q(c, 'action-run-note-r1').textContent).toMatch(/this thread, 12 h/)
  })

  it('Approve once sends scope "once"', async () => {
    api.listActionApprovals.mockImplementation(async () => [runCard()])
    api.approveAction.mockImplementation(async () => runCard({ status: 'approved', scope: 'once' }))
    const c = await mount()
    await click(btn(c, 'Approve once'))
    expect(api.approveAction).toHaveBeenCalledWith('r1', 'once')
  })

  it('an agent-filed or cut request (no verified key) offers Approve once only, with the reason', async () => {
    api.listActionApprovals.mockImplementation(async () => [card({ run_keys: [{ rule_id: 'git-reset-hard', key_kind: 'command', key: '' }] })])
    const c = await mount()
    expect(btn(c, 'Approve for this run').disabled).toBe(true)
    expect(btn(c, 'Approve once').disabled).toBe(false)
    await act(async () => {
      q(c, 'action-run-r1').dispatchEvent(new MouseEvent('mouseover', { bubbles: true }))
      await vi.advanceTimersByTimeAsync(300)
    })
    expect(document.body.textContent).toMatch(/can't be approved for the whole run/)
  })

  it('no run_keys at all → run disabled too', async () => {
    const c = await mount()
    expect(btn(c, 'Approve for this run').disabled).toBe(true)
  })

  it('a quarantine card explains the run grant', async () => {
    api.listActionApprovals.mockImplementation(async () => [
      card({ rule_ids: ['guest-quarantine'], run_keys: [{ rule_id: 'guest-quarantine', key_kind: 'rule', key: '*' }] }),
    ])
    const c = await mount()
    await act(async () => {
      q(c, 'action-run-r1').dispatchEvent(new MouseEvent('mouseover', { bubbles: true }))
      await vi.advanceTimersByTimeAsync(300)
    })
    expect(document.body.textContent).toContain("Lets this repo's code run in this thread for 12 h")
  })

  it('the resume phase shows the badge (no countdown) and stays decidable past expires_at', async () => {
    api.listActionApprovals.mockImplementation(async () => [
      runCard({ delivery: 'resume', expires_at: '2026-11-21T09:00:00Z', resume_until: '2026-11-22T09:00:00Z' }),
    ])
    api.approveAction.mockImplementation(async () =>
      runCard({ status: 'approved', delivery: 'resume', followup_task_id: 'f1', can_decide: false }),
    )
    const c = await mount()
    expect(q(c, 'action-resume-r1').textContent).toBe('Agent stopped — approving starts a follow-up')
    expect(q(c, 'action-countdown-r1')).toBeNull()
    expect(q(c, 'action-resume-left-r1').textContent).toContain('23 h')
    await click(btn(c, 'Approve once'))
    await flush()
    expect(api.approveAction).toHaveBeenCalledWith('r1', 'once')
    expect(q(c, 'action-followup-r1').querySelector('a[href="#/tasks/f1"]')).not.toBeNull()
  })

  it('a deferred resume approval says the follow-up starts when the step ends', async () => {
    api.listActionApprovals.mockImplementation(async () => [card({ delivery: 'resume', resume_until: '2026-11-22T09:00:00Z' })])
    api.approveAction.mockImplementation(async () => card({ status: 'approved', delivery: 'resume', followup_task_id: null }))
    const c = await mount()
    await click(btn(c, 'Approve once'))
    await flush()
    expect(q(c, 'action-followup-r1').textContent).toMatch(/current step ends/)
  })

  it('an inline request with resume_until says what happens if nobody answers, then moves to the resume phase', async () => {
    api.listActionApprovals.mockImplementation(async () => [card({ resume_until: '2026-11-22T09:59:00Z' })])
    const c = await mount()
    expect(q(c, 'action-resume-note-r1').textContent).toMatch(/24 h/)
    expect(q(c, 'action-resume-r1')).toBeNull()
    await tick(121_000)
    expect(q(c, 'action-resume-r1')).not.toBeNull()
    expect(q(c, 'action-state-r1')).toBeNull() // not expired
  })

  it('bidi / zero-width characters are shown as markers with a warning chip and need a confirmation', async () => {
    api.listActionApprovals.mockImplementation(async () => [card({ summary: 'echo safe‮;rm -rf x​', cwd: '/w⁦' })])
    api.approveAction.mockImplementation(async () => card({ status: 'approved' }))
    const c = await mount()
    const sum = q(c, 'action-summary-r1').textContent
    expect(sum).toBe('echo safe⟦U+202E⟧;rm -rf x⟦U+200B⟧')
    expect(q(c, 'action-hidden-r1').textContent).toContain('hidden characters')
    expect(q(c, 'action-card-r1').textContent).toContain('/w⟦U+2066⟧')
    await click(btn(c, 'Approve once'))
    await flush()
    expect(api.approveAction).not.toHaveBeenCalled()
    expect(document.body.textContent).toContain('hidden characters')
    await click(btn(document.body, 'Approve anyway'))
    await flush()
    expect(api.approveAction).toHaveBeenCalledWith('r1', 'once')
  })

  it('no chip for ordinary text', async () => {
    const c = await mount()
    expect(q(c, 'action-hidden-r1')).toBeNull()
  })
})
