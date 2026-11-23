import { act } from 'react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { flush, render } from '../home/testRender'

vi.mock('../api', () => ({
  api: { listProposals: vi.fn(), acceptProposal: vi.fn(), rejectProposal: vi.fn() },
}))
import { api } from '../api'
import ProposalsQueue from './ProposalsQueue'

const prop = (id, over = {}) => ({
  id, source: 'run', kind: 'decision', title: `title ${id}`, why: `why ${id}`, touches: ['src/a/**'], evidence: 'commits abc1234',
  status: 'pending', task_title: 'Port it', proposed_by_name: null, ...over,
})
const project = { id: 'p1' }
const q = (c, id) => c.querySelector(`[data-testid="${id}"]`)
const click = (el) => act(async () => { el.dispatchEvent(new MouseEvent('click', { bubbles: true })) })
async function type(el, value) {
  await act(async () => {
    const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype
    Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, value)
    el.dispatchEvent(new Event('input', { bubbles: true }))
  })
}
let view
async function mount(props = {}) {
  view = await render(<ProposalsQueue project={project} canEdit {...props} />)
  await flush()
  return view.container
}
beforeAll(() => {
  window.matchMedia ||= () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} })
})
beforeEach(() => {
  vi.clearAllMocks()
  api.listProposals.mockResolvedValue({ items: [prop('a'), prop('b', { source: 'answer' })], pending: 2 })
  api.acceptProposal.mockResolvedValue({})
  api.rejectProposal.mockResolvedValue({})
})
afterEach(async () => {
  await view?.unmount()
  view = null
})

describe('ProposalsQueue', () => {
  it('renders nothing with 0 pending and reports the count', async () => {
    api.listProposals.mockResolvedValue({ items: [], pending: 0 })
    const onCount = vi.fn()
    const c = await mount({ onCount })
    expect(q(c, 'proposals-queue')).toBeNull()
    expect(onCount).toHaveBeenCalledWith(0)
    expect(api.listProposals).toHaveBeenCalledWith('p1', 'pending')
  })
  it('shows heading, origins, touches, evidence', async () => {
    const onCount = vi.fn()
    const c = await mount({ onCount })
    expect(c.textContent).toContain('Suggested (2)')
    expect(c.textContent).toContain('from run Port it')
    expect(c.textContent).toContain('from an answer')
    expect(c.textContent).toContain('src/a/**')
    expect(c.textContent).toContain('commits abc1234')
    expect(onCount).toHaveBeenCalledWith(2)
  })
  it('viewers have no Accept or Reject', async () => {
    const c = await mount({ canEdit: false })
    expect(q(c, 'proposal-accept-a')).toBeNull()
    expect(q(c, 'proposal-reject-a')).toBeNull()
    expect(c.textContent).toContain('title a')
  })
  it('Accept opens a pre-filled form; Save sends the edited body', async () => {
    const onAccepted = vi.fn()
    const c = await mount({ onAccepted })
    await click(q(c, 'proposal-accept-a'))
    expect(document.body.textContent).toContain('Accept suggestion')
    expect(q(document.body, 'decision-title').value).toBe('title a')
    expect(q(document.body, 'decision-source')).toBeNull()
    await type(q(document.body, 'decision-title'), 'edited')
    await click(q(document.body, 'decision-save'))
    await flush()
    expect(api.acceptProposal).toHaveBeenCalledWith('p1', 'a', { kind: 'decision', title: 'edited', why: 'why a', see: null, touches: ['src/a/**'] })
    expect(onAccepted).toHaveBeenCalled()
    expect(api.listProposals.mock.calls.length).toBeGreaterThan(1)
  })
  it('Reject calls the API and reloads', async () => {
    const c = await mount()
    await click(q(c, 'proposal-reject-a'))
    await flush()
    expect(api.rejectProposal).toHaveBeenCalledWith('p1', 'a')
    expect(api.listProposals.mock.calls.length).toBe(2)
  })
  it('a 409 shows the message and reloads', async () => {
    const err = Object.assign(new Error('this suggestion was already reviewed'), { status: 409 })
    api.rejectProposal.mockRejectedValue(err)
    const c = await mount()
    await click(q(c, 'proposal-reject-a'))
    await flush()
    expect(q(c, 'proposals-notice').textContent).toContain('this suggestion was already reviewed')
    expect(api.listProposals.mock.calls.length).toBe(2)
  })
  it('a 409 on accept shows the message, closes the form and reloads', async () => {
    api.acceptProposal.mockRejectedValue(Object.assign(new Error('this suggestion was already reviewed'), { status: 409 }))
    const c = await mount()
    await click(q(c, 'proposal-accept-a'))
    await click(q(document.body, 'decision-save'))
    await flush()
    expect(q(c, 'proposals-notice').textContent).toContain('this suggestion was already reviewed')
    expect(api.listProposals.mock.calls.length).toBe(2)
  })
})
