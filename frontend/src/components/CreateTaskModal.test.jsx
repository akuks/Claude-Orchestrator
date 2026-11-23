import { act } from 'react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { flush, render } from '../home/testRender'

vi.mock('../api', () => ({
  api: {
    createTask: vi.fn(async (b) => b),
    scopeCheck: vi.fn(),
    addLaunchItem: vi.fn(),
    getLaunch: vi.fn(),
  },
}))
vi.mock('antd', async (orig) => {
  const a = await orig()
  return { ...a, message: { ...a.message, error: vi.fn(), success: vi.fn() } }
})

import { message } from 'antd'
import { api } from '../api'
import CreateTaskModal from './CreateTaskModal'

const setter = (proto) => Object.getOwnPropertyDescriptor(proto, 'value').set
async function type(el, value, proto = HTMLTextAreaElement.prototype) {
  await act(async () => {
    setter(proto).call(el, value)
    el.dispatchEvent(new Event('input', { bubbles: true }))
  })
}
const submit = async () => {
  const b = [...document.querySelectorAll('.ant-modal button')].find((x) => x.textContent.includes('Run Task'))
  await act(async () => b.click())
  await flush()
}
async function addTag(id, value) {
  const input = document.querySelector(`#${id}`)
  await type(input, value, HTMLInputElement.prototype)
  await act(async () => {
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', keyCode: 13, which: 13, bubbles: true }))
  })
}
let view
beforeAll(() => {
  window.matchMedia ||= () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} })
})
beforeEach(() => {
  vi.clearAllMocks()
  api.scopeCheck.mockResolvedValue({ new_scope: false, open: 0, would_be: 1, launch_date: null })
  api.getLaunch.mockResolvedValue({ launch: null, items: [], all_linked_done: {} })
})
afterEach(async () => {
  await view?.unmount()
  view = null
  document.body.innerHTML = ''
})
const open = async () => {
  view = await render(<CreateTaskModal open onClose={() => {}} onCreated={() => {}} />)
  await flush()
  await type(document.querySelector('#prompt'), 'do it')
}

describe('CreateTaskModal brief', () => {
  it('sends no brief for an empty section', async () => {
    await open()
    await submit()
    expect(api.createTask).toHaveBeenCalledTimes(1)
    expect(api.createTask.mock.calls[0][0]).not.toHaveProperty('brief')
  })
  it('sends the brief when filled', async () => {
    await open()
    await type(document.querySelector('#brief_goal'), 'ship')
    await type(document.querySelector('#brief_done'), 'tests pass')
    await type(document.querySelector('#brief_stop'), 'if unsure')
    await addTag('brief_can_change', 'src/**')
    await submit()
    expect(api.createTask.mock.calls[0][0].brief).toEqual({
      goal: 'ship', can_change: ['src/**'], cannot_change: [], done: 'tests pass', stop: 'if unsure',
    })
  })
  it('shows an inline error and does not call when stop is missing', async () => {
    await open()
    await type(document.querySelector('#brief_goal'), 'ship')
    await type(document.querySelector('#brief_done'), 'ok')
    await submit()
    expect(api.createTask).not.toHaveBeenCalled()
    expect(document.querySelector('[data-testid="brief-error"]').textContent).toContain('stop')
  })
  it('shows the server 422 text inline', async () => {
    api.createTask.mockRejectedValueOnce(new Error('can_change may have at most 20 paths'))
    await open()
    await type(document.querySelector('#brief_goal'), 'ship')
    await type(document.querySelector('#brief_done'), 'ok')
    await type(document.querySelector('#brief_stop'), 'stop')
    await submit()
    expect(document.querySelector('[data-testid="brief-error"]').textContent).toContain('at most 20 paths')
  })
  it('keeps the section open when a collapsed section has an error', async () => {
    await open()
    await type(document.querySelector('#brief_goal'), 'ship')
    const header = () => document.querySelector('.ant-collapse-header')
    await act(async () => header().click()) // open
    await act(async () => header().click()) // collapse
    expect(document.querySelector('.ant-collapse-item-active')).toBeNull()
    await submit()
    expect(api.createTask).not.toHaveBeenCalled()
    expect(document.querySelector('[data-testid="brief-error"]')).not.toBeNull()
    expect(document.querySelector('.ant-collapse-item-active')).not.toBeNull()
  })
  it('shows a server error inline and as a toast when a brief was sent', async () => {
    api.createTask.mockRejectedValueOnce(new Error('the brief needs goal'))
    await open()
    await type(document.querySelector('#brief_goal'), 'ship')
    await type(document.querySelector('#brief_done'), 'ok')
    await type(document.querySelector('#brief_stop'), 'stop')
    await submit()
    expect(document.querySelector('[data-testid="brief-error"]').textContent).toContain('the brief needs goal')
    expect(message.error).toHaveBeenCalledWith('the brief needs goal')
  })
  it('toasts only when no brief was sent', async () => {
    api.createTask.mockRejectedValueOnce(new Error('boom'))
    await open()
    await submit()
    expect(message.error).toHaveBeenCalledWith('boom')
    expect(document.querySelector('[data-testid="brief-error"]')).toBeNull()
  })
})

describe('CreateTaskModal launch nudge', () => {
  const q = (id) => document.querySelector(`[data-testid="${id}"]`)
  const openP = async () => {
    view = await render(<CreateTaskModal open onClose={() => {}} onCreated={() => {}} projects={[{ id: 'p1', name: 'P' }]} activeProjectId="p1" />)
    await flush()
    await type(document.querySelector('#prompt'), 'Add a billing page\nwith more detail')
  }
  const nudgeIt = () => api.scopeCheck.mockResolvedValue({ new_scope: true, open: 4, would_be: 7, launch_date: null })

  it('shows the exact nudge copy and does not create the task yet', async () => {
    nudgeIt()
    await openP()
    await submit()
    expect(api.scopeCheck).toHaveBeenCalledWith('p1', 'Add a billing page\nwith more detail')
    expect(q('scope-nudge-text').textContent).toBe('This looks like new scope: the launch list has 4 open items; this would become #7.')
    expect(api.createTask).not.toHaveBeenCalled()
  })
  it('Add to launch list creates the item first, then the linked task', async () => {
    nudgeIt()
    api.addLaunchItem.mockResolvedValue({ id: 'li9', number: 7 })
    await openP()
    await submit()
    await act(async () => q('nudge-add').click())
    await flush()
    expect(api.addLaunchItem).toHaveBeenCalledWith('p1', { title: 'Add a billing page', section: 'before' })
    expect(api.createTask).toHaveBeenCalledTimes(1)
    expect(api.createTask.mock.calls[0][0].launch_item_id).toBe('li9')
    expect(api.addLaunchItem.mock.invocationCallOrder[0]).toBeLessThan(api.createTask.mock.invocationCallOrder[0])
  })
  it('After launch adds an after item', async () => {
    nudgeIt()
    api.addLaunchItem.mockResolvedValue({ id: 'li9', number: 7 })
    await openP()
    await submit()
    await act(async () => q('nudge-after').click())
    await flush()
    expect(api.addLaunchItem).toHaveBeenCalledWith('p1', { title: 'Add a billing page', section: 'after' })
    expect(api.createTask.mock.calls[0][0].launch_item_id).toBe('li9')
  })
  it('Run anyway creates only the task', async () => {
    nudgeIt()
    await openP()
    await submit()
    await act(async () => q('nudge-run').click())
    await flush()
    expect(api.addLaunchItem).not.toHaveBeenCalled()
    expect(api.createTask).toHaveBeenCalledTimes(1)
    expect(api.createTask.mock.calls[0][0].launch_item_id).toBeUndefined()
  })
  it('no nudge when scope-check says false', async () => {
    await openP()
    await submit()
    expect(q('scope-nudge')).toBeNull()
    expect(api.createTask).toHaveBeenCalledTimes(1)
  })
  it('a scope-check error never blocks the submit', async () => {
    api.scopeCheck.mockRejectedValue(new Error('down'))
    await openP()
    await submit()
    expect(q('scope-nudge')).toBeNull()
    expect(api.createTask).toHaveBeenCalledTimes(1)
  })
  it('no scope-check without a project', async () => {
    await open()
    await submit()
    expect(api.scopeCheck).not.toHaveBeenCalled()
    expect(api.createTask).toHaveBeenCalledTimes(1)
  })
  it('lists open and started items in a Launch item select', async () => {
    api.getLaunch.mockResolvedValue({
      launch: null,
      items: [
        { id: 'a', number: 1, title: 'one', status: 'open', section: 'before' },
        { id: 'b', number: 2, title: 'two', status: 'done', section: 'before' },
      ],
      all_linked_done: {},
    })
    await openP()
    await flush()
    expect(document.querySelector('#launch_item_id')).not.toBeNull()
    await act(async () => {
      document.querySelector('#launch_item_id').dispatchEvent(new MouseEvent('mousedown', { bubbles: true }))
    })
    const opts = [...document.querySelectorAll('.ant-select-item-option')].map((o) => o.textContent)
    expect(opts).toEqual(['#1 one'])
  })
})
