import { act } from 'react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { flush, render } from '../home/testRender'

vi.mock('../api', () => ({
  api: { getLaunch: vi.fn(), saveLaunch: vi.fn(), addLaunchItem: vi.fn(), updateLaunchItem: vi.fn(), deleteLaunchItem: vi.fn() },
}))
import { api } from '../api'
import LaunchTab from './LaunchTab'

const item = (n, over = {}) => ({ id: `i${n}`, number: n, title: `item ${n}`, section: 'before', status: 'open', started_at: null, done_at: null, version: 2, ...over })
const data = () => ({
  launch: { launch_date: '2026-12-01', critical_paths: ['src/billing/**'], version: 3 },
  items: [item(1), item(2, { section: 'after' })],
  all_linked_done: { 1: true },
})
const q = (c, id) => c.querySelector(`[data-testid="${id}"]`)
const click = (el) => act(async () => el.dispatchEvent(new MouseEvent('click', { bubbles: true })))
async function type(el, value) {
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(el, value)
    el.dispatchEvent(new Event('input', { bubbles: true }))
  })
}
let view
const mount = async (props = {}) => {
  view = await render(<LaunchTab project={{ id: 'p1' }} canEdit {...props} />)
  await flush()
  return view.container
}
beforeAll(() => {
  window.matchMedia ||= () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} })
})
beforeEach(() => {
  vi.clearAllMocks()
  api.getLaunch.mockResolvedValue(data())
  api.addLaunchItem.mockResolvedValue({})
  api.updateLaunchItem.mockResolvedValue({})
})
afterEach(async () => {
  await view?.unmount()
  view = null
})

describe('LaunchTab', () => {
  it('renders both sections with rows and the linked-done hint', async () => {
    const c = await mount()
    expect(q(c, 'launch-section-before').textContent).toContain('Before launch')
    expect(q(c, 'launch-section-after').textContent).toContain('After launch')
    expect(q(c, 'launch-section-before').textContent).toContain('#1')
    expect(q(c, 'launch-section-after').textContent).toContain('item 2')
    expect(q(c, 'launch-hint-1').textContent).toContain('linked tasks done — mark done?')
    expect(q(c, 'launch-hint-2')).toBeNull()
  })
  it('a contributor does not see Delete, Edit or the date inputs', async () => {
    const c = await mount({ canEdit: false })
    expect(q(c, 'launch-start-1')).not.toBeNull()
    expect(q(c, 'launch-delete-1')).toBeNull()
    expect(q(c, 'launch-edit-1')).toBeNull()
    expect(q(c, 'launch-date')).toBeNull()
  })
  it('a maintainer sees Delete', async () => {
    const c = await mount()
    expect(q(c, 'launch-delete-1')).not.toBeNull()
  })
  it('Add calls the api', async () => {
    const c = await mount()
    await type(q(c, 'launch-add-input-before'), 'new thing')
    await click(q(c, 'launch-add-before'))
    await flush()
    expect(api.addLaunchItem).toHaveBeenCalledWith('p1', { title: 'new thing', section: 'before' })
    expect(api.getLaunch).toHaveBeenCalledTimes(2)
  })
  it('Start sends the version and the new status', async () => {
    const c = await mount()
    await click(q(c, 'launch-start-1'))
    await flush()
    expect(api.updateLaunchItem).toHaveBeenCalledWith('p1', 1, { version: 2, status: 'started' })
  })
  it('a 409 reloads and shows the message', async () => {
    api.updateLaunchItem.mockRejectedValue(Object.assign(new Error('x'), { status: 409 }))
    const c = await mount()
    await click(q(c, 'launch-done-1'))
    await flush()
    expect(api.getLaunch).toHaveBeenCalledTimes(2)
    expect(q(c, 'launch-error').textContent).toContain('changed by someone else — reload')
  })
  it('Save sends the date, the paths and the version', async () => {
    api.saveLaunch.mockResolvedValue({})
    const c = await mount()
    await click(q(c, 'launch-save'))
    await flush()
    expect(api.saveLaunch).toHaveBeenCalledWith('p1', { launch_date: '2026-12-01', critical_paths: ['src/billing/**'], version: 3 })
  })
  it('a reload after an action keeps an unsaved date draft', async () => {
    const c = await mount()
    await type(q(c, 'launch-date'), '2027-01-01')
    await click(q(c, 'launch-start-1'))
    await flush()
    expect(q(c, 'launch-date').value).toBe('2027-01-01')
  })
  it('a 409 resets the drafts', async () => {
    api.updateLaunchItem.mockRejectedValue(Object.assign(new Error('x'), { status: 409 }))
    const c = await mount()
    await type(q(c, 'launch-date'), '2027-01-01')
    await click(q(c, 'launch-done-1'))
    await flush()
    expect(q(c, 'launch-date').value).toBe('2026-12-01')
  })
  it('shows hidden characters in titles', async () => {
    api.getLaunch.mockResolvedValue({ ...data(), items: [item(1, { title: 'a\u202Eb' })] })
    const c = await mount()
    expect(q(c, 'launch-item-1').textContent).toContain('⟦U+202E⟧')
  })
})

describe('LaunchTab: critical files raise the risk', () => {
  it('a maintainer turns it on: PUT with the stored date, paths, version and raise_risk', async () => {
    api.saveLaunch.mockResolvedValue({})
    const c = await mount()
    expect(c.textContent).toContain('Treat tasks that mention a critical file as high risk')
    expect(c.textContent).toContain('Such tasks are marked critical, which can require approval.')
    const sw = q(c, 'launch-raise-risk')
    expect(sw.getAttribute('aria-checked')).toBe('false')
    // an unsaved date draft is not sent with the switch
    await type(q(c, 'launch-date'), '2027-01-01')
    await click(sw)
    await flush()
    expect(api.saveLaunch).toHaveBeenCalledWith('p1', { launch_date: '2026-12-01', critical_paths: ['src/billing/**'], version: 3, raise_risk: true })
  })
  it('shows the stored value and turns it off', async () => {
    api.saveLaunch.mockResolvedValue({})
    api.getLaunch.mockResolvedValue({ ...data(), launch: { ...data().launch, raise_risk: true } })
    const c = await mount()
    expect(q(c, 'launch-raise-risk').getAttribute('aria-checked')).toBe('true')
    await click(q(c, 'launch-raise-risk'))
    await flush()
    expect(api.saveLaunch.mock.calls[0][1].raise_risk).toBe(false)
  })
  it('a contributor does not see the switch', async () => {
    const c = await mount({ canEdit: false })
    expect(q(c, 'launch-raise-risk')).toBeNull()
  })
})
