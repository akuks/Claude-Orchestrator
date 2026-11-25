import { act } from 'react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { flush, render } from '../home/testRender'

vi.mock('../api', () => ({
  api: { getMyPreferences: vi.fn(), saveMyPreferences: vi.fn() },
}))

import { api } from '../api'
import MyPreferences from './MyPreferences'

const body = () => document.body
const q = (id) => document.querySelector(`[data-testid="${id}"]`)
const click = (el) => act(async () => { el.dispatchEvent(new MouseEvent('click', { bubbles: true })) })

let view
async function mount(onClose = () => {}) {
  view = await render(<MyPreferences open onClose={onClose} />)
  await flush()
  await flush()
}
const type = async (el, value) => {
  await act(async () => {
    const set = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set
    set.call(el, value)
    el.dispatchEvent(new Event('input', { bubbles: true }))
  })
}

beforeAll(() => {
  window.matchMedia ||= () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} })
})
beforeEach(() => {
  vi.clearAllMocks()
  api.getMyPreferences.mockImplementation(async () => ({
    enabled: true,
    items: [{ id: 'a', text: 'Short answers', on: true }],
    updated_at: null,
    preview: '# Preferences\n- Short answers',
  }))
})
afterEach(async () => { await view?.unmount?.(); view = null })

describe('MyPreferences', () => {
  it('shows the intro, the list and the counter', async () => {
    await mount()
    expect(body().textContent).toContain('Short notes about how you like to work.')
    expect(body().textContent).toContain('1 of 12')
    expect(q('pref-text-0').value).toBe('Short answers')
    expect(body().textContent).toContain('Save to update')
  })

  it('saves the whole list and shows the server preview and the success line', async () => {
    api.saveMyPreferences.mockImplementation(async (b) => ({ ...b, updated_at: 'now', preview: 'PREVIEW-FROM-SERVER' }))
    await mount()
    await click(q('pref-add'))
    await type(q('pref-text-1'), 'Explain tradeoffs')
    await click(q('pref-save'))
    await flush()
    expect(api.saveMyPreferences).toHaveBeenCalledWith({
      enabled: true,
      items: [{ id: 'a', text: 'Short answers', on: true }, { text: 'Explain tradeoffs', on: true }],
    })
    expect(body().textContent).toContain('Saved. Your next tasks will use these.')
    expect(body().textContent).toContain('PREVIEW-FROM-SERVER')
  })

  it('deletes an item', async () => {
    api.saveMyPreferences.mockImplementation(async (b) => ({ ...b, preview: null }))
    await mount()
    await click(q('pref-delete-0'))
    await click(q('pref-save'))
    await flush()
    expect(api.saveMyPreferences).toHaveBeenCalledWith({ enabled: true, items: [] })
  })

  it('stops adding at 12', async () => {
    api.getMyPreferences.mockImplementation(async () => ({
      enabled: true, items: Array.from({ length: 12 }, (_, i) => ({ id: `i${i}`, text: `t${i}`, on: true })), preview: null,
    }))
    await mount()
    expect(body().textContent).toContain('12 of 12')
    expect(q('pref-add').disabled).toBe(true)
  })

  it('shows the 422 reason plainly', async () => {
    api.saveMyPreferences.mockImplementation(async () => { throw Object.assign(new Error('a preference cannot be empty'), { status: 422 }) })
    await mount()
    await type(q('pref-text-0'), '')
    await click(q('pref-save'))
    await flush()
    expect(q('pref-error').textContent).toContain('a preference cannot be empty')
  })

  it('does not let a newline in', async () => {
    await mount()
    await type(q('pref-text-0'), 'one\ntwo')
    expect(q('pref-text-0').value).toBe('one two')
  })
})
