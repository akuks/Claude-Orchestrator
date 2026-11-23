import { act } from 'react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { flush, render } from '../home/testRender'

vi.mock('../api', () => ({ api: { createTask: vi.fn(async (b) => b) } }))

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
beforeEach(() => vi.clearAllMocks())
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
})
