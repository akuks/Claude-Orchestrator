import { act } from 'react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { flush, render } from '../home/testRender'
import { when } from '../time'

vi.mock('../api', () => ({
  api: { getHandoff: vi.fn(), refreshHandoff: vi.fn(), closeHandoff: vi.fn() },
}))
import { api } from '../api'
import HandoffCard from './HandoffCard'

const H = {
  status: 'open',
  version: 3,
  saved_at: '2026-11-22T10:00:00+00:00',
  content: {
    task: 'Ship the thing',
    step: { n: 2, m: 4, text: 'wire the UI' },
    done: ['wrote api'],
    next: 'add tests',
    running: ['npm run dev'],
    waiting: ['approval for rm'],
    notes: { text: 'my <b>notes</b>‮hidden', at: '2026-11-22T09:30:00+00:00' },
    files: ['src/a.js'],
    outside_docs: ['/tmp/plan.md'],
    outside_commits: [{ sha: 'abc123', subject: 'fix x' }],
  },
}
const q = (c, id) => c.querySelector(`[data-testid="${id}"]`)
const click = (el) => act(async () => { el.dispatchEvent(new MouseEvent('click', { bubbles: true })) })
let view
const mount = async (props = {}) => {
  view = await render(<HandoffCard taskId="t1" canManage {...props} />)
  await flush()
  await flush()
  return view.container
}
beforeAll(() => {
  window.matchMedia ||= () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} })
})
beforeEach(() => {
  vi.clearAllMocks()
  api.getHandoff.mockResolvedValue(H)
})
afterEach(async () => {
  await view?.unmount()
  view = null
})

describe('HandoffCard', () => {
  it('asks for no handoff while no task is selected', async () => {
    const c = await mount({ taskId: null })
    expect(api.getHandoff).not.toHaveBeenCalled()
    expect(q(c, 'handoff-card')).toBeNull()
  })

  it('renders the fields and the notes time from content.notes.at', async () => {
    const onHandoff = vi.fn()
    const c = await mount({ onHandoff })
    expect(q(c, 'handoff-task').textContent).toContain('Ship the thing')
    expect(q(c, 'handoff-step').textContent).toContain('Step 2 of 4: wire the UI')
    expect(q(c, 'handoff-done').textContent).toContain('wrote api')
    expect(q(c, 'handoff-next').textContent).toContain('add tests')
    expect(q(c, 'handoff-running').textContent).toContain('npm run dev')
    expect(q(c, 'handoff-waiting').textContent).toContain('approval for rm')
    expect(q(c, 'handoff-files').textContent).toContain('src/a.js')
    expect(q(c, 'handoff-temp').textContent).toContain('may vanish')
    expect(q(c, 'handoff-commits').textContent).toContain('abc123 fix x')
    expect(q(c, 'handoff-notes-at').textContent).toContain(when(H.content.notes.at).format('MMM D, HH:mm'))
    expect(onHandoff).toHaveBeenCalledWith(H)
  })
  it('shows notes as plain text with hidden characters revealed', async () => {
    const c = await mount()
    const n = q(c, 'handoff-notes')
    expect(n.textContent).toContain('my <b>notes</b>')
    expect(n.querySelector('b')).toBeNull()
    expect(n.textContent).toContain('⟦U+202E⟧')
    expect(n.textContent).not.toContain('‮')
  })
  it('Mark done and Refresh call the API', async () => {
    api.closeHandoff.mockResolvedValue({ ...H, status: 'done' })
    api.refreshHandoff.mockResolvedValue(H)
    const c = await mount()
    await click(q(c, 'handoff-close'))
    await flush()
    expect(api.closeHandoff).toHaveBeenCalledWith('t1')
    expect(q(c, 'handoff-close')).toBeNull()
    await click(q(c, 'handoff-refresh'))
    expect(api.refreshHandoff).toHaveBeenCalledWith('t1')
  })
  it('asks again when the step ends, not on every render', async () => {
    const c = await mount({ status: undefined })
    await view.rerender(<HandoffCard taskId="t1" canManage status="running" />) // the task loads: no new fetch
    expect(api.getHandoff).toHaveBeenCalledTimes(1)
    api.getHandoff.mockResolvedValue({ ...H, reason: 'run ended', content: { ...H.content, task: 'finished task' } })
    await view.rerender(<HandoffCard taskId="t1" canManage status="completed" />)
    await flush()
    expect(api.getHandoff).toHaveBeenCalledTimes(2)
    expect(q(c, 'handoff-task').textContent).toContain('finished task')
    expect(q(c, 'handoff-meta').textContent).toContain('· run ended') // the header says why it was saved
    await view.rerender(<HandoffCard taskId="t1" canManage status="completed" />)
    await flush()
    expect(api.getHandoff).toHaveBeenCalledTimes(2)
    await view.rerender(<HandoffCard taskId="t2" canManage status="running" />)
    await view.rerender(<HandoffCard taskId="t3" canManage status="failed" />) // another task: one fetch
    await flush()
    expect(api.getHandoff).toHaveBeenCalledTimes(4)
  })

  it('shows an empty state on null', async () => {
    api.getHandoff.mockResolvedValue(null)
    const c = await mount()
    expect(q(c, 'handoff-empty')).not.toBeNull()
    expect(q(c, 'handoff-close')).toBeNull()
  })
  it('hides Refresh and Mark done from a viewer who cannot manage', async () => {
    const c = await mount({ canManage: false })
    expect(q(c, 'handoff-task')).not.toBeNull()
    expect(q(c, 'handoff-refresh')).toBeNull()
    expect(q(c, 'handoff-close')).toBeNull()
  })
  it('collapses', async () => {
    const c = await mount()
    await click(q(c, 'handoff-toggle'))
    expect(q(c, 'handoff-task')).toBeNull()
  })
  it('shows an action error', async () => {
    api.refreshHandoff.mockRejectedValue(new Error('nope'))
    const c = await mount()
    await click(q(c, 'handoff-refresh'))
    await flush()
    expect(q(c, 'handoff-error').textContent).toBe('nope')
  })
})
