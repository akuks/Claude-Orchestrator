import { act } from 'react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { flush, render } from '../home/testRender'

vi.mock('../api', () => ({ api: { continueTask: vi.fn() } }))
import { api } from '../api'
import ContinueButton from './ContinueButton'

const task = { id: 't1', status: 'failed', origin: 'user', created_by: 'u1', session_id: 's' }
const q = (id) => document.body.querySelector(`[data-testid="${id}"]`)
const click = (el) => act(async () => { el.dispatchEvent(new MouseEvent('click', { bubbles: true })) })
const item = (text) => [...document.body.querySelectorAll('.ant-dropdown-menu-item')].find((e) => e.textContent === text)
let view
const mount = async (props) => {
  view = await render(<ContinueButton task={task} user={{ id: 'u1' }} thread={[task]} {...props} />)
  await flush()
}
beforeAll(() => {
  window.matchMedia ||= () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} })
})
beforeEach(() => vi.clearAllMocks())
afterEach(async () => {
  await view?.unmount()
  view = null
})

describe('ContinueButton', () => {
  it('is hidden on completed, agent and active-sibling steps', async () => {
    await mount({ task: { ...task, status: 'completed' } })
    expect(q('continue-button')).toBeNull()
    await view.unmount()
    await mount({ task: { ...task, origin: 'agent' } })
    expect(q('continue-button')).toBeNull()
    await view.unmount()
    await mount({ thread: [task, { id: 'x', status: 'running' }] })
    expect(q('continue-button')).toBeNull()
  })
  it('both modes call continueTask and open the child', async () => {
    api.continueTask.mockResolvedValue({ id: 'child' })
    const onContinued = vi.fn()
    await mount({ onContinued })
    await click(q('continue-button'))
    await click(item('Continue (resume)'))
    await flush()
    expect(api.continueTask).toHaveBeenLastCalledWith('t1', 'resume')
    expect(onContinued).toHaveBeenCalledWith({ id: 'child' })
    await click(q('continue-button'))
    await click(item('Continue fresh with the handoff'))
    await flush()
    expect(api.continueTask).toHaveBeenLastCalledWith('t1', 'fresh')
  })
  it('resume is disabled for a non-owner', async () => {
    await mount({ user: { id: 'u2' } })
    await click(q('continue-button'))
    expect(item('Continue (resume)').className).toContain('disabled')
    expect(item('Continue fresh with the handoff').className).not.toContain('disabled')
  })
  it('shows a 409 message', async () => {
    api.continueTask.mockRejectedValue(Object.assign(new Error('A step is already active'), { status: 409 }))
    await mount()
    await click(q('continue-button'))
    await click(item('Continue (resume)'))
    await flush()
    expect(q('continue-error').textContent).toContain('A step is already active')
  })
})
