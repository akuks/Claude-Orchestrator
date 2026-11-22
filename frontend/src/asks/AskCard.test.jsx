import { act } from 'react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { flush, render } from '../home/testRender'

vi.mock('../api', () => ({ api: { taskAsks: vi.fn(), askTask: vi.fn() } }))
vi.mock('antd', async (orig) => {
  const m = await orig()
  return { ...m, message: { error: vi.fn(), success: vi.fn() } }
})
import { message } from 'antd'
import { api } from '../api'
import AskCard, { askCost } from './AskCard'

const $ = (c, id) => c.querySelector(`[data-testid="${id}"]`)
const click = (el) => act(async () => el.dispatchEvent(new MouseEvent('click', { bubbles: true })))
const type = (el, value) =>
  act(async () => {
    const set = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set
    set.call(el, value)
    el.dispatchEvent(new Event('input', { bubbles: true }))
  })
const ask = (o) => ({ task_id: 't1', answer: null, error: null, cost_usd: 0, status: 'answered', ...o })

let view
const mount = async () => {
  view = await render(<AskCard taskId="t1" />)
  await flush()
  return view.container
}
beforeAll(() => {
  window.matchMedia ||= () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} })
})
beforeEach(() => {
  vi.clearAllMocks()
  api.taskAsks.mockResolvedValue([])
})
afterEach(async () => {
  vi.useRealTimers()
  await view?.unmount()
  view = null
})

describe('AskCard', () => {
  it('renders hint and asks newest first', async () => {
    api.taskAsks.mockResolvedValue([
      ask({ id: 'a2', question: 'Second?', answer: 'Two' }),
      ask({ id: 'a1', question: 'First?', answer: 'One' }),
    ])
    const c = await mount()
    expect($(c, 'ask-hint').textContent).toBe("Answered from the run's record, not the code. Only you see this.")
    const items = [...c.querySelectorAll('li[data-testid^="ask-"]')].map((li) => li.querySelector('div').textContent)
    expect(items).toEqual(['Second?', 'First?'])
    expect($(c, 'ask-input').getAttribute('maxlength')).toBe('2000')
    expect($(c, 'ask-submit').disabled).toBe(true)
  })

  it('ask posts and polls until answered', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: false })
    api.askTask.mockResolvedValue(ask({ id: 'a1', question: 'Why?', status: 'pending' }))
    const c = await mount()
    await type($(c, 'ask-input'), 'Why?')
    await click($(c, 'ask-submit'))
    expect(api.askTask).toHaveBeenCalledWith('t1', 'Why?')
    expect($(c, 'ask-pending')).not.toBeNull()
    expect($(c, 'ask-submit').disabled).toBe(true)
    api.taskAsks.mockResolvedValue([ask({ id: 'a1', question: 'Why?', answer: 'Because.', cost_usd: 0.0123 })])
    const calls = api.taskAsks.mock.calls.length
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000)
    })
    expect(api.taskAsks.mock.calls.length).toBe(calls + 1)
    expect($(c, 'ask-answer').textContent).toBe('Because.')
    expect($(c, 'ask-cost').textContent).toBe('$0.01')
    await act(async () => {
      await vi.advanceTimersByTimeAsync(6000)
    })
    expect(api.taskAsks.mock.calls.length).toBe(calls + 1) // no polling once none is pending
  })

  it('try again re-asks the same question', async () => {
    api.taskAsks.mockResolvedValue([ask({ id: 'a1', question: 'Why?', status: 'failed', error: 'Model unavailable' })])
    api.askTask.mockResolvedValue(ask({ id: 'a2', question: 'Why?', status: 'pending' }))
    const c = await mount()
    expect($(c, 'ask-error').textContent).toBe('Model unavailable')
    await click($(c, 'ask-retry'))
    expect(api.askTask).toHaveBeenCalledWith('t1', 'Why?')
    expect($(c, 'ask-a2')).not.toBeNull()
  })

  it('shows server error detail', async () => {
    api.askTask.mockRejectedValue(Object.assign(new Error('Too many questions; wait a minute'), { status: 429 }))
    const c = await mount()
    await type($(c, 'ask-input'), 'Hi?')
    await click($(c, 'ask-submit'))
    expect(message.error).toHaveBeenCalledWith('Too many questions; wait a minute')
  })

  it('after a 409 the list is reloaded so the pending ask shows', async () => {
    api.askTask.mockRejectedValue(Object.assign(new Error('You already have a question pending'), { status: 409 }))
    const c = await mount()
    await type($(c, 'ask-input'), 'Hi?')
    api.taskAsks.mockResolvedValue([ask({ id: 'p1', question: 'Other tab?', status: 'pending' })])
    await click($(c, 'ask-submit'))
    await flush()
    expect(message.error).toHaveBeenCalled()
    expect($(c, 'ask-pending')).not.toBeNull()
  })

  it('cost formatting', () => {
    expect(askCost(0.0123)).toBe('$0.01')
    expect(askCost(0.001)).toBe('<$0.01')
    expect(askCost(0.004999)).toBe('<$0.01')
    expect(askCost(0.005)).toBe('$0.01')
    expect(askCost(1.5)).toBe('$1.50')
    expect(askCost(0)).toBeNull()
    expect(askCost(null)).toBeNull()
  })

  it('a late response for the previous task does not touch the new task', async () => {
    let resolveA
    api.taskAsks.mockImplementation((id) =>
      id === 'a' ? new Promise((r) => (resolveA = r)) : Promise.resolve([ask({ id: 'b1', task_id: 'b', question: 'B?', answer: 'bee' })]),
    )
    view = await render(<AskCard taskId="a" />)
    await flush()
    await view.rerender(<AskCard taskId="b" />)
    await flush()
    await act(async () => resolveA([ask({ id: 'a1', question: 'A?', answer: 'ay' })]))
    const c = view.container
    expect($(c, 'ask-b1')).not.toBeNull()
    expect($(c, 'ask-a1')).toBeNull()
  })

  it('a late ask response for the previous task is dropped and sending resets', async () => {
    let resolveAsk
    api.askTask.mockImplementation(() => new Promise((r) => (resolveAsk = r)))
    view = await render(<AskCard taskId="a" />)
    await flush()
    await type($(view.container, 'ask-input'), 'Hi?')
    await click($(view.container, 'ask-submit'))
    await view.rerender(<AskCard taskId="b" />)
    await flush()
    await act(async () => resolveAsk(ask({ id: 'a9', question: 'Hi?', status: 'pending' })))
    expect($(view.container, 'ask-a9')).toBeNull()
    await type($(view.container, 'ask-input'), 'Next?')
    expect($(view.container, 'ask-submit').disabled).toBe(false)
  })
})
