import { act } from 'react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { flush, render } from '../home/testRender'

vi.mock('../api', () => ({
  api: {
    getTask: vi.fn(async (id) => ({ id, title: `Task ${id}`, status: 'running', prompt: 'p' })),
    getThread: vi.fn(async () => []),
    getArtifacts: vi.fn(async () => []),
    taskActionApprovals: vi.fn(async () => []),
    userDirectory: vi.fn(async () => [{ id: 'u2', name: 'Bea', email: 'b@x.io' }]),
    streamUrl: (id, seq) => `ws://test/tasks/${id}/stream?after=${seq}`,
    reportUrl: () => '#',
    artifactUrl: () => '#',
  },
  notifyUnauthorized: vi.fn(),
  STATUS_COLORS: {},
  PRIORITY_COLORS: {},
  shortModel: (m) => m,
}))

import { api } from '../api'
import TaskDetail from './TaskDetail'

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
