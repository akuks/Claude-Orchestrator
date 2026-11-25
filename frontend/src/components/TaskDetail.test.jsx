import { act } from 'react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { flush, render } from '../home/testRender'

vi.mock('../api', () => ({
  api: {
    getTask: vi.fn(async (id) => ({ id, title: `Task ${id}`, status: 'running', prompt: 'p' })),
    getThread: vi.fn(async () => []),
    getArtifacts: vi.fn(async () => []),
    taskActionApprovals: vi.fn(async () => []),
    getHandoff: vi.fn(async () => null),
    refreshHandoff: vi.fn(),
    closeHandoff: vi.fn(),
    continueTask: vi.fn(),
    getUsage: vi.fn(async () => null),
    taskQuestions: vi.fn(async () => []),
    taskAsks: vi.fn(async () => []),
    askTask: vi.fn(),
    answerQuestion: vi.fn(),
    dismissQuestion: vi.fn(),
    userDirectory: vi.fn(async () => [{ id: 'u2', name: 'Bea', email: 'b@x.io' }]),
    streamUrl: (id, seq) => `ws://test/tasks/${id}/stream?after=${seq}`,
    reportUrl: () => '#',
    artifactUrl: () => '#',
    getLaunch: vi.fn(async () => ({ items: [] })),
    setTaskLaunchItem: vi.fn(),
  },
  notifyUnauthorized: vi.fn(),
  STATUS_COLORS: {},
  PRIORITY_COLORS: {},
  shortModel: (m) => m,
}))

import { api } from '../api'
import TaskDetail, { renderLine } from './TaskDetail'

// A fake socket that records what the component opens and closes.
class FakeSocket {
  static all = []
  constructor(url) {
    this.url = url
    this.closed = false
    FakeSocket.all.push(this)
  }
  close() {
    this.closed = true
  }
}

describe('TaskDetail inline: live stream lifecycle', () => {
  let view
  const RealWS = globalThis.WebSocket
  beforeAll(() => {
    window.matchMedia ||= () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} })
  })
  afterEach(async () => {
    await view?.unmount()
    view = null
    globalThis.WebSocket = RealWS
    FakeSocket.all = []
  })

  it('closes the old socket on task switch and the current one on unmount', async () => {
    globalThis.WebSocket = FakeSocket
    view = await render(<TaskDetail inline taskId="a" />)
    await flush()
    expect(FakeSocket.all.map((s) => s.url)).toEqual(['ws://test/tasks/a/stream?after=0'])
    expect(view.container.querySelector('[data-testid="task-detail-inline"]').textContent).toContain('Task a')

    await view.rerender(<TaskDetail inline taskId="b" />)
    await flush()
    expect(FakeSocket.all).toHaveLength(2)
    expect(FakeSocket.all[0].closed).toBe(true)
    expect(FakeSocket.all[1].url).toContain('/tasks/b/')
    expect(FakeSocket.all[1].closed).toBe(false)
    expect(view.container.textContent).toContain('Task b')
    expect(view.container.textContent).not.toContain('Task a')

    await view.unmount()
    view = null
    expect(FakeSocket.all[1].closed).toBe(true)
    expect(FakeSocket.all).toHaveLength(2)
  })
})

describe('TaskDetail: plan limit pause', () => {
  let view
  const RealWS = globalThis.WebSocket
  afterEach(async () => {
    await view?.unmount()
    view = null
    globalThis.WebSocket = RealWS
    FakeSocket.all = []
  })
  it('shows the paused message with local time from not_before, and plan_limit events as plain lines', async () => {
    globalThis.WebSocket = FakeSocket
    const nb = new Date(Date.now() + 2 * 3600e3).toISOString()
    api.getTask.mockImplementationOnce(async (id) => ({ id, title: 'T', status: 'queued', prompt: 'p', not_before: nb, wait_reason: 'server 03:33' }))
    view = await render(<TaskDetail inline taskId="a" />)
    await flush()
    const m = view.container.querySelector('[data-testid="task-plan-paused"]')
    expect(m.textContent).toMatch(/Paused — your Claude plan limit was reached\. Starts again at (tomorrow |\d+ \w+, )?\d{2}:\d{2}\./)
    expect(m.textContent).not.toContain('03:33')
    await act(async () => FakeSocket.all.at(-1).onmessage({ data: JSON.stringify({ seq: 1, type: 'plan_limit', payload: { status: 'rejected', resets_at: nb, raw: { a: 1 } } }) }))
    const l = view.container.querySelector('[data-testid="plan-limit-line"]')
    expect(l.textContent).toMatch(/^Your Claude plan limit was reached\. Work starts again at/)
    expect(l.textContent).not.toMatch(/[{}"]/)
    const calls = api.getTask.mock.calls.length
    await act(async () => FakeSocket.all.at(-1).onmessage({ data: JSON.stringify({ seq: 2, type: 'plan_limit', payload: { paused: true, not_before: nb } }) }))
    expect(api.getTask.mock.calls.length).toBe(calls + 1)
  })
})

describe('TaskDetail: co-guard lines', () => {
  let view
  const RealWS = globalThis.WebSocket
  afterEach(async () => {
    await view?.unmount()
    view = null
    globalThis.WebSocket = RealWS
    FakeSocket.all = []
  })

  const send = (ev) => act(async () => FakeSocket.all.at(-1).onmessage({ data: JSON.stringify(ev) }))
  const line = (c, kind) => [...c.querySelectorAll(`[data-testid="guard-line-${kind}"]`)]

  it('renders each guard event kind as its own line with an icon', async () => {
    globalThis.WebSocket = FakeSocket
    api.taskActionApprovals.mockImplementation(async () => [
      { id: 'r1', status: 'approved', decided_by: 'u2' },
      { id: 'r2', status: 'rejected', decided_by: 'u2', decision_reason: 'nope' },
      { id: 'r4', status: 'pending', tool: 'Bash', summary: '<b>git push --force</b>', rule_ids: ['git-force-push-main'], expires_at: '2099-01-01T00:00:00Z', can_decide: true },
      { id: 'r5', status: 'pending', tool: 'Bash', summary: 'chmod -R 777 .', rule_ids: ['chmod-wide'], expires_at: '2099-01-01T00:00:00Z', can_decide: false },
    ])
    view = await render(<TaskDetail inline taskId="a" />)
    await flush()
    const base = { event: 'PreToolUse', tool: 'Bash', summary: 'rm -rf ~' }
    let seq = 1
    for (const payload of [
      { ...base, decision: 'deny', rule_ids: ['rm-rf-outside'], reason: 'home (rm-rf-outside)' },
      { ...base, decision: 'approved', status: 'approved', request_id: 'r1', summary: 'git reset --hard' },
      { ...base, decision: 'deny', status: 'rejected', request_id: 'r2' },
      { ...base, decision: 'deny', status: 'timeout', request_id: 'r3' },
      { event: 'settings_override', reason: 'project settings switch hooks off', files: ['.claude/settings.json'] },
      { event: 'PreToolUse', decision: 'deny', rule_ids: [], reason: 'co-guard error' },
      { event: 'watchdog', decision: 'killed', reason: 'Bash call x ran without a co-guard check' },
    ])
      await send({ seq: seq++, type: 'guard', payload })
    await flush()
    const c = view.container
    for (const kind of ['blocked', 'approved', 'rejected', 'expired', 'override', 'error', 'watchdog'])
      expect(line(c, kind).length, kind).toBe(1)
    expect(line(c, 'waiting').length).toBe(2)
    // not decidable by the viewer: no link, says who decides
    const theirs = line(c, 'waiting')[1]
    expect(theirs.querySelector('a')).toBeNull()
    expect(theirs.textContent).toContain('waiting for a maintainer to decide')
    for (const el of c.querySelectorAll('[data-testid^="guard-line-"]')) expect(el.querySelector('.anticon')).not.toBeNull()
    expect(line(c, 'approved')[0].textContent).toContain('approved by Bea')
    expect(line(c, 'rejected')[0].textContent).toContain('nope')
    expect(line(c, 'override')[0].textContent).toContain('.claude/settings.json')
    // waiting: the pending request, linked to its card, as plain text
    const waiting = line(c, 'waiting')[0]
    expect(waiting.querySelector('a[href="#/approvals/r4"]')).not.toBeNull()
    expect(waiting.textContent).toContain('<b>git push --force</b>')
    expect(waiting.querySelector('b')).toBeNull()
    expect(api.taskActionApprovals).toHaveBeenCalledWith('a')
  })

  it('granted and filed lines (Phase 2), plain text, the filed one linked when decidable', async () => {
    globalThis.WebSocket = FakeSocket
    api.taskActionApprovals.mockImplementation(async () => [
      { id: 'r7', status: 'pending', delivery: 'resume', tool: 'Bash', summary: 'psql -h prod -c "delete from t"', rule_ids: ['sql-destroy'], can_decide: true, expires_at: '2020-01-01T00:00:00Z' },
    ])
    view = await render(<TaskDetail inline taskId="a" />)
    await flush()
    let seq = 1
    for (const payload of [
      { event: 'PreToolUse', decision: 'granted', grant_ids: ['g1'], tool: 'Bash', summary: '<i>git reset --hard</i>', rule_ids: ['git-reset-hard'] },
      { event: 'PreToolUse', decision: 'deny', tool: 'Bash', summary: 'echo ok\u202e ;rm -rf x', rule_ids: [], reason: 'r' },
      { event: 'PreToolUse', decision: 'deny', status: 'filed', request_id: 'r7', tool: 'Bash', summary: 'psql -h prod', rule_ids: ['sql-destroy'] },
    ])
      await send({ seq: seq++, type: 'guard', payload })
    await flush()
    const c = view.container
    const granted = line(c, 'granted')
    expect(granted.length).toBe(1)
    expect(granted[0].textContent).toContain('<i>git reset --hard</i>')
    expect(granted[0].querySelector('i')).toBeNull()
    expect(granted[0].textContent).toContain('allowed by an earlier approval (grant g1)')
    expect(granted[0].querySelector('.anticon')).not.toBeNull()
    const filed = line(c, 'filed')
    expect(filed.length).toBe(1)
    expect(filed[0].textContent).toMatch(/the agent stopped/)
    expect(filed[0].querySelector('a[href="#/approvals/r7"]')).not.toBeNull()
    expect(line(c, 'waiting')[0].textContent).toMatch(/approving starts a follow-up/)
    // bidi controls cannot reorder a run-log line
    expect(line(c, 'blocked')[0].textContent).toContain('echo ok⟦U+202E⟧ ;rm -rf x')
  })
})

describe('TaskDetail: approvals enrichment refetch', () => {
  const RealWS = globalThis.WebSocket
  afterEach(() => {
    globalThis.WebSocket = RealWS
    FakeSocket.all = []
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'visible' })
  })

  it('refetches the run\'s requests as soon as the tab becomes visible', async () => {
    globalThis.WebSocket = FakeSocket
    api.taskActionApprovals.mockClear()
    api.taskActionApprovals.mockImplementation(async () => [])
    const view = await render(<TaskDetail inline taskId="a" />)
    await flush()
    const before = api.taskActionApprovals.mock.calls.length
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'visible' })
    await act(async () => document.dispatchEvent(new Event('visibilitychange')))
    expect(api.taskActionApprovals.mock.calls.length).toBe(before + 1)
    await view.unmount()
  })
})

describe('TaskDetail: where it stands (phase 3a)', () => {
  let view
  const RealWS = globalThis.WebSocket
  afterEach(async () => {
    await view?.unmount()
    view = null
    globalThis.WebSocket = RealWS
    FakeSocket.all = []
  })

  it('mounts the handoff card, the notices and Continue for the owner of a failed step', async () => {
    globalThis.WebSocket = FakeSocket
    api.getTask.mockImplementationOnce(async (id) => ({
      id, title: 'T', status: 'failed', prompt: 'p', origin: 'user', created_by: 'u1', session_id: 's', can_manage: true, can_resume: true,
    }))
    api.getHandoff.mockImplementationOnce(async () => ({ status: 'open', content: { task: 'do it', no_commits: true } }))
    view = await render(<TaskDetail inline taskId="a" currentUser={{ id: 'u1' }} />)
    await flush()
    await flush()
    const c = view.container
    expect(c.querySelector('[data-testid="handoff-card"]').textContent).toContain('do it')
    expect(c.querySelector('[data-testid="notice-no_commits"]')).not.toBeNull()
    expect(c.querySelector('[data-testid="continue-button"]')).not.toBeNull()
  })

  it('opens the new step after Continue', async () => {
    globalThis.WebSocket = FakeSocket
    api.getTask.mockImplementationOnce(async (id) => ({
      id, title: 'T', status: 'failed', prompt: 'p', origin: 'user', created_by: 'u1', session_id: 's', can_manage: true, can_resume: true,
    }))
    api.continueTask.mockResolvedValue({ id: 'child' })
    const onOpenTask = vi.fn()
    view = await render(<TaskDetail inline taskId="a" currentUser={{ id: 'u1' }} onOpenTask={onOpenTask} />)
    await flush()
    await flush()
    const click = (el) => act(async () => { el.dispatchEvent(new MouseEvent('click', { bubbles: true })) })
    await click(view.container.querySelector('[data-testid="continue-button"]'))
    await click([...document.body.querySelectorAll('.ant-dropdown-menu-item')].find((e) => e.textContent === 'Continue (resume)'))
    await flush()
    expect(api.continueTask).toHaveBeenCalledWith('a', 'resume')
    expect(onOpenTask).toHaveBeenCalledWith('child')
  })
})

describe('TaskDetail: token care (phase 3b)', () => {
  let view
  const RealWS = globalThis.WebSocket
  afterEach(async () => {
    vi.useRealTimers()
    await view?.unmount()
    view = null
    globalThis.WebSocket = RealWS
    FakeSocket.all = []
    api.getUsage.mockReset()
    api.getUsage.mockImplementation(async () => null)
  })
  const send = (ev) => act(async () => FakeSocket.all.at(-1).onmessage({ data: JSON.stringify(ev) }))
  const usage = {
    step: { context_tokens: 130000, peak_context: 130000, est_cost_usd: 0.5, cost_is_estimate: true, max_budget_usd: 1, budget_pct: 50, fresh_session_tokens: 150000, breakdown: null },
    thread: { steps: 3, duration_ms: 41 * 60000, cost_usd: 2.1, tokens: 1_200_000, last: { title: 'T', duration_ms: 18 * 60000, cost_usd: 1.4 } },
    tips: ['keep the start of prompts stable'],
  }

  it('shows the chips in the header, the so-far line and the tokens panel', async () => {
    globalThis.WebSocket = FakeSocket
    api.getUsage.mockImplementation(async () => usage)
    view = await render(<TaskDetail inline taskId="a" />)
    await flush()
    const c = view.container
    const head = c.querySelector('.co-task-inline-head')
    expect(head.querySelector('[data-testid="usage-context"]').textContent).toBe('context 130k')
    expect(head.querySelector('[data-testid="usage-context"]').className).toContain('co-usage-chip-amber')
    expect(head.querySelector('[data-testid="usage-budget"]').textContent).toBe('budget 50 % est.')
    expect(c.querySelector('[data-testid="usage-so-far"]').textContent).toContain('3 steps · 41 min · $2.10 · 1.2M tokens')
    expect(c.querySelector('[data-testid="tokens-panel"]')).not.toBeNull()
    expect(api.getUsage).toHaveBeenCalledWith('a')
  })

  it('polls the usage every 5 s while the step runs, once otherwise', async () => {
    vi.useFakeTimers()
    globalThis.WebSocket = FakeSocket
    api.getUsage.mockImplementation(async () => usage)
    view = await render(<TaskDetail inline taskId="a" />)
    await flush()
    const n0 = api.getUsage.mock.calls.length
    await act(async () => vi.advanceTimersByTime(5000))
    expect(api.getUsage.mock.calls.length).toBe(n0 + 1)
    await act(async () => vi.advanceTimersByTime(5000))
    expect(api.getUsage.mock.calls.length).toBe(n0 + 2)
    await view.unmount()
    api.getTask.mockImplementationOnce(async (id) => ({ id, title: 'Done', status: 'completed', prompt: 'p' }))
    api.getUsage.mockClear()
    view = await render(<TaskDetail inline taskId="b" />)
    await flush()
    await act(async () => vi.advanceTimersByTime(20000))
    expect(api.getUsage.mock.calls.length).toBe(1)
    // finished without a result cost: still an estimate (the server says so)
    expect(view.container.querySelector('[data-testid="usage-budget"]').textContent).toBe('budget 50 % est.')
  })

  it('renders usage_band and auto_continue events as their own lines', async () => {
    globalThis.WebSocket = FakeSocket
    view = await render(<TaskDetail inline taskId="a" />)
    await flush()
    await send({ seq: 1, type: 'usage_band', payload: { band: 'budget80', value: 0.8 } })
    await send({ seq: 2, type: 'usage_band', payload: { band: 'budget95', value: 0.95 } })
    await send({ seq: 3, type: 'usage_band', payload: { band: 'fresh', value: 152000 } })
    await send({ seq: 4, type: 'auto_continue', payload: { text: 'continued automatically: context reached 152k', context_tokens: 152000 } })
    const c = view.container
    const one = (kind) => c.querySelectorAll(`[data-testid="usage-line-${kind}"]`)
    for (const kind of ['budget80', 'budget95', 'fresh', 'auto']) {
      expect(one(kind).length, kind).toBe(1)
      expect(one(kind)[0].querySelector('.anticon'), kind).not.toBeNull()
    }
    expect(one('budget80')[0].textContent).toContain('budget about 80 % used ($0.80)')
    expect(one('fresh')[0].textContent).toContain('context reached 152k')
    expect(one('auto')[0].textContent).toBe('continued automatically: context reached 152k')
    expect(c.querySelector('.co-terminal').textContent).not.toContain('{"band"')
  })
})

describe('TaskDetail: the thread\'s questions (phase 3b)', () => {
  let view
  const RealWS = globalThis.WebSocket
  afterEach(async () => {
    await view?.unmount()
    view = null
    globalThis.WebSocket = RealWS
    FakeSocket.all = []
  })

  it('shows an open question as a card; an answer opens the follow-up step', async () => {
    globalThis.WebSocket = FakeSocket
    api.getTask.mockImplementation(async (id) => ({ id, title: 'T', status: 'completed', prompt: 'p' }))
    api.taskQuestions.mockImplementation(async () => [{
      id: 'q1', task_id: 'a', status: 'open', can_answer: true, question: 'Which table?', options: ['old', 'new'],
      recommendation: 'new', source: 'final_text', created_at: '2026-11-22T10:00:00Z',
    }])
    api.answerQuestion.mockResolvedValue({ question: {}, task: { id: 'child' } })
    const onOpenTask = vi.fn()
    view = await render(<TaskDetail inline taskId="a" onOpenTask={onOpenTask} />)
    await flush()
    await flush()
    const card = view.container.querySelector('[data-testid="thread-questions"]')
    expect(api.taskQuestions).toHaveBeenCalledWith('a')
    expect(card.textContent).toContain('Which table?')
    await act(async () => card.querySelector('[data-testid="question-accept"]').dispatchEvent(new MouseEvent('click', { bubbles: true })))
    await flush()
    expect(api.answerQuestion).toHaveBeenCalledWith('q1', { accept: true })
    expect(onOpenTask).toHaveBeenCalledWith('child')
    api.taskQuestions.mockImplementation(async () => [])
    api.getTask.mockReset()
    api.getTask.mockImplementation(async (id) => ({ id, title: `Task ${id}`, status: 'running', prompt: 'p' }))
  })

  it('completed event refetches until terminal then reloads questions', async () => {
    globalThis.WebSocket = FakeSocket
    view = await render(<TaskDetail inline taskId="a" />)
    await flush()
    vi.useFakeTimers()
    api.getTask.mockReset()
    api.getTask
      .mockResolvedValueOnce({ id: 'a', title: 'T', status: 'running', prompt: 'p' })
      .mockResolvedValue({ id: 'a', title: 'T', status: 'completed', prompt: 'p' })
    api.taskQuestions.mockClear()
    api.getThread.mockClear()
    api.getUsage.mockClear()
    await act(async () => FakeSocket.all[0].onmessage({ data: JSON.stringify({ seq: 1, type: 'completed', payload: { status: 'completed' } }) }))
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    expect(api.getTask).toHaveBeenCalledTimes(1)
    expect(api.taskQuestions).not.toHaveBeenCalled()
    await act(async () => { await vi.advanceTimersByTimeAsync(500) })
    expect(api.getTask).toHaveBeenCalledTimes(2)
    await act(async () => { await vi.advanceTimersByTimeAsync(10) })
    expect(api.taskQuestions).toHaveBeenCalled()
    expect(api.getThread).toHaveBeenCalled()
    expect(api.getUsage).toHaveBeenCalled()
    await act(async () => { await vi.advanceTimersByTimeAsync(6000) })
    expect(api.getTask).toHaveBeenCalledTimes(2) // stopped early
    vi.useRealTimers()
    api.getTask.mockReset()
    api.getTask.mockImplementation(async (id) => ({ id, title: `Task ${id}`, status: 'running', prompt: 'p' }))
  })
})

describe('decision_review run-log line', () => {
  it('renders the review line', () => {
    expect(renderLine({ type: 'decision_review', payload: { proposed: 2, stale: [4] } }).text).toBe('Suggested 2 decisions for review · flagged D-004 as maybe out of date')
    expect(renderLine({ type: 'output', payload: { link: 'https://x.io/a', tool: 'mcp__n__create_page', title: 'Plan', local_file: null, kept: false } }).text).toBe('Published: Plan')
    expect(renderLine({ type: 'test_result', payload: { runner: 'pytest', passed: 61, failed: 2, skipped: 0, failing: ['a', 'b'] } }).text).toBe('Tests: 61 passed, 2 failed (a, b)')
    expect(renderLine({ type: 'test_result', payload: { runner: 'go', passed: null, failed: 0, skipped: 3, failing: [] } }).text).toBe('Tests: 3 skipped')
    expect(renderLine({ type: 'test_result', payload: { runner: 'go', passed: null, failed: null, skipped: null } }).text).toBe('Tests: none ran')
  })
})

describe('code_map run-log line', () => {
  it('says how fresh the answer was and which decisions it showed', () => {
    const line = (payload) => renderLine({ type: 'code_map', payload }).text
    expect(line({ fresh: true, decisions: [], old_code: false })).toBe('Code map answer: current')
    expect(line({ fresh: false, decisions: [12], old_code: true })).toBe('Code map answer: may be out of date · showed D-012')
    expect(line({ fresh: null, decisions: [12, 15] })).toBe('Code map answer: freshness unknown · showed D-012, D-015')
    expect(line({})).toBe('Code map answer: freshness unknown')
  })
  it('says when some recorded decisions were not checked', () => {
    const line = (type, payload) => renderLine({ type, payload }).text
    expect(line('code_map', { fresh: true, decisions: [12], partial: true })).toBe('Code map answer: current · showed D-012 · some recorded decisions were not checked')
    expect(line('code_map', { fresh: null, decisions: [], partial: 'yes' })).toBe('Code map answer: freshness unknown')
    expect(line('decision_note', { ids: [3], path: 'src/a.py', partial: true })).toBe('Showed D-003 before editing src/a.py · some recorded decisions were not checked')
    expect(line('decision_note', { ids: [3], path: 'src/a.py' })).toBe('Showed D-003 before editing src/a.py')
  })
})

describe('critical_paths run-log line', () => {
  it('renders the chip text', () => {
    expect(renderLine({ type: 'critical_paths', payload: { paths: ['src/a.py', 'src/b.py'], globs: ['src/**'] } }).text).toBe(
      'This run changed 2 critical path(s): src/a.py, src/b.py',
    )
  })
})

describe('TaskDetail: brief', () => {
  let view
  const RealWS = globalThis.WebSocket
  afterEach(async () => {
    await view?.unmount()
    view = null
    globalThis.WebSocket = RealWS
    FakeSocket.all = []
  })
  const send = (ev) => act(async () => FakeSocket.all.at(-1).onmessage({ data: JSON.stringify(ev) }))

  it('shows the brief panel with an inherited bound', async () => {
    globalThis.WebSocket = FakeSocket
    api.getTask.mockImplementationOnce(async (id) => ({
      id, title: 'T', status: 'running', prompt: 'p',
      brief: { goal: 'Ship it', can_change: ['src/**'], cannot_change: ['db/**'], done: 'd', stop: 's' },
      brief_bounds: [{ task_id: 'parent1', can_change: ['src/**'], cannot_change: ['secrets/**'] }],
    }))
    view = await render(<TaskDetail inline taskId="a" />)
    await flush()
    const panel = view.container.querySelector('[data-testid="brief-panel"]')
    expect(panel.textContent).toContain('Ship it')
    expect(panel.textContent).toContain('src/**')
    expect(view.container.querySelector('[data-testid="brief-bound"]').textContent).toContain('Also bound by an enclosing brief parent1')
  })
  it('renders brief guard events', async () => {
    globalThis.WebSocket = FakeSocket
    view = await render(<TaskDetail inline taskId="a" />)
    await flush()
    await send({ seq: 1, type: 'guard', payload: { event: 'PreToolUse', tool: 'Edit', summary: 'db/x.sql', decision: 'deny', rule_ids: ['brief-scope'], reason: 'no' } })
    await send({ seq: 2, type: 'guard', payload: { event: 'brief_scope', shell_roots: 2, file_tools_only: [] } })
    await flush()
    const text = view.container.textContent
    expect(text).toContain('Outside the task brief')
    expect(text).toContain('Brief: 2 CANNOT folders also guarded for shell commands')
  })
})

describe('TaskDetail: launch item and risk', () => {
  let view
  const RealWS = globalThis.WebSocket
  beforeAll(() => {
    window.matchMedia ||= () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} })
  })
  afterEach(async () => {
    await view?.unmount()
    view = null
    globalThis.WebSocket = RealWS
    FakeSocket.all = []
    api.getTask.mockReset()
    api.getTask.mockImplementation(async (id) => ({ id, title: `Task ${id}`, status: 'running', prompt: 'p' }))
  })
  const send = (ev) => act(async () => FakeSocket.all.at(-1).onmessage({ data: JSON.stringify(ev) }))
  const items = [
    { id: 'i1', number: 1, title: 'Billing page', status: 'open' },
    { id: 'i2', number: 2, title: 'Emails', status: 'started' },
    { id: 'i3', number: 3, title: 'Old thing', status: 'done' },
  ]
  const task = (over = {}) => ({ id: 'a', title: 'T', status: 'running', prompt: 'p', project_id: 'p1', project: 'Proj', can_manage: true, launch_item_id: null, ...over })
  const openSelect = async () => {
    await act(async () => {
      view.container.querySelector('[data-testid="task-launch-item"] .ant-select-selector').dispatchEvent(new MouseEvent('mousedown', { bubbles: true }))
    })
    await flush()
  }
  const pick = async (label) => {
    const opt = [...document.body.querySelectorAll('.ant-select-item-option')].find((o) => o.textContent === label)
    await act(async () => opt.dispatchEvent(new MouseEvent('click', { bubbles: true })))
    await flush()
  }

  it('lists open and started items plus None, and PATCHes the link', async () => {
    globalThis.WebSocket = FakeSocket
    api.getTask.mockImplementation(async () => task())
    api.getLaunch.mockResolvedValue({ items })
    api.setTaskLaunchItem.mockResolvedValue(task({ launch_item_id: 'i2' }))
    view = await render(<TaskDetail inline taskId="a" />)
    await flush()
    expect(api.getLaunch).toHaveBeenCalledWith('p1')
    await openSelect()
    const labels = [...document.body.querySelectorAll('.ant-select-item-option')].map((o) => o.textContent)
    expect(labels).toEqual(['None', '#1 Billing page', '#2 Emails'])
    await pick('#2 Emails')
    expect(api.setTaskLaunchItem).toHaveBeenCalledWith('a', 'i2')
    expect(view.container.querySelector('[data-testid="task-launch-item"]').textContent).toContain('#2 Emails')
  })

  it('None unlinks; a refusal shows the server reason', async () => {
    globalThis.WebSocket = FakeSocket
    api.getTask.mockImplementation(async () => task({ launch_item_id: 'i1' }))
    api.getLaunch.mockResolvedValue({ items })
    api.setTaskLaunchItem.mockRejectedValue(Object.assign(new Error("launch_item_id must be an open or started item of the task's project"), { status: 422 }))
    view = await render(<TaskDetail inline taskId="a" />)
    await flush()
    await openSelect()
    await pick('None')
    expect(api.setTaskLaunchItem).toHaveBeenCalledWith('a', null)
    expect(view.container.querySelector('[data-testid="task-launch-item-error"]').textContent).toContain('must be an open or started item')
  })

  it("the task's creator gets the select even without can_manage", async () => {
    globalThis.WebSocket = FakeSocket
    api.getTask.mockImplementation(async () => task({ can_manage: false, created_by: 'u1' }))
    api.getLaunch.mockResolvedValue({ items })
    api.setTaskLaunchItem.mockResolvedValue(task({ can_manage: false, created_by: 'u1', launch_item_id: 'i1' }))
    view = await render(<TaskDetail inline taskId="a" currentUser={{ id: 'u1' }} />)
    await flush()
    await openSelect()
    await pick('#1 Billing page')
    expect(api.setTaskLaunchItem).toHaveBeenCalledWith('a', 'i1')
    await view.unmount()
    view = await render(<TaskDetail inline taskId="a" currentUser={{ id: 'u2' }} />)
    await flush()
    expect(view.container.querySelector('[data-testid="task-launch-item"]')).toBeNull()
  })

  it('hidden from people who cannot change it, and for tasks without a project', async () => {
    globalThis.WebSocket = FakeSocket
    api.getTask.mockImplementationOnce(async () => task({ can_manage: false }))
    view = await render(<TaskDetail inline taskId="a" />)
    await flush()
    expect(view.container.querySelector('[data-testid="task-launch-item"]')).toBeNull()
    await view.unmount()
    api.getTask.mockImplementationOnce(async () => task({ project_id: null }))
    view = await render(<TaskDetail inline taskId="a" />)
    await flush()
    expect(view.container.querySelector('[data-testid="task-launch-item"]')).toBeNull()
  })

  it('renders the risk event as a plain line', async () => {
    expect(renderLine({ type: 'risk', payload: { risk: 'critical', risk_reason: 'critical path src/billing' } }).text).toBe(
      'Marked critical: critical path src/billing',
    )
    globalThis.WebSocket = FakeSocket
    view = await render(<TaskDetail inline taskId="a" />)
    await flush()
    await send({ seq: 1, type: 'risk', payload: { risk: 'critical', risk_reason: 'critical path src/billing' } })
    expect(view.container.querySelector('.co-terminal').textContent).toContain('Marked critical: critical path src/billing')
  })
})

describe('summary run-log line', () => {
  it('is one plain line, never JSON', () => {
    const l = renderLine({ type: 'summary', payload: { events: 30, counts: { text_output: 30 }, lines: ['a'], kept_days: 60 } })
    expect(l.text).toBe('Older run history kept as a summary (30 events).')
    expect(l.text).not.toContain('{')
  })
})
