import { act } from 'react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { flush, render } from '../home/testRender'

vi.mock('../api', () => ({ api: { updateProject: vi.fn(async (id, v) => ({ id, ...v })) } }))

import { api } from '../api'
import GuardSettings from './GuardSettings'

const project = (over = {}) => ({ id: 'p1', name: 'P', guard_enabled: true, approval_wait_seconds: 300, my_role: 'maintainer', ...over })
const q = (c, id) => c.querySelector(`[data-testid="${id}"]`)
const btn = (c, label) => [...c.querySelectorAll('button')].find((b) => b.textContent.trim() === label)
const click = (el) => act(async () => { el.dispatchEvent(new MouseEvent('click', { bubbles: true })) })

let view
async function mount(props) {
  view = await render(<GuardSettings {...props} />)
  await flush()
  return view.container
}

beforeAll(() => {
  window.matchMedia ||= () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} })
})
beforeEach(() => vi.clearAllMocks())
afterEach(async () => {
  await view?.unmount()
  view = null
})

describe('GuardSettings', () => {
  it('shows the help text and the current values', async () => {
    const c = await mount({ project: project({ approval_wait_seconds: 90 }), canEdit: true })
    expect(q(c, 'guard-help').textContent).toMatch(/self-protection/)
    expect(q(c, 'guard-help').textContent).toContain('!`cmd`')
    expect(q(c, 'guard-wait-label').textContent).toContain('1 min 30 s')
    expect(q(c, 'guard-enabled').getAttribute('aria-checked')).toBe('true')
  })

  it('is read-only for contributors', async () => {
    const c = await mount({ project: project({ my_role: 'contributor' }), canEdit: false })
    expect(q(c, 'guard-enabled').disabled).toBe(true)
    expect(q(c, 'guard-wait-input').querySelector('input').disabled).toBe(true)
    expect(btn(c, 'Save guard settings')).toBeUndefined()
    expect(q(c, 'guard-readonly').textContent).toMatch(/maintainers/i)
  })

  it('maintainers save guard_enabled and the wait', async () => {
    const onSaved = vi.fn()
    const c = await mount({ project: project(), canEdit: true, onSaved })
    await click(q(c, 'guard-enabled'))
    expect(q(c, 'guard-off-warning')).not.toBeNull()
    await click(btn(c, 'Save guard settings'))
    await flush()
    expect(api.updateProject).toHaveBeenCalledWith('p1', { guard_enabled: false, approval_wait_seconds: 300 })
    expect(onSaved).toHaveBeenCalled()
  })
})
