import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { flush, render } from '../home/testRender'

vi.mock('../api', () => ({
  api: {
    listUsers: vi.fn(),
    planStatusAll: vi.fn(),
    planTryNow: vi.fn(async () => ({ removed: true })),
  },
}))
vi.mock('./OrgFallbackCard', () => ({ default: () => null }))

import { api } from '../api'
import UsersView from './UsersView'

const iso = (h) => new Date(Date.now() + h * 3600e3).toISOString()
let view
beforeAll(() => {
  window.matchMedia ||= () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} })
})
afterEach(async () => {
  await view?.unmount()
  view = null
})

describe('UsersView: Claude plan tags', () => {
  it('tags, org row first, Try now with confirmation calls DELETE and refetches', async () => {
    api.listUsers.mockResolvedValue([
      { id: 'u1', name: 'Asha', email: 'a@x.io', role: 'member', active: true },
      { id: 'u2', name: 'Bea', email: 'b@x.io', role: 'member', active: true },
      { id: 'u3', name: 'Cy', email: 'c@x.io', role: 'member', active: true },
    ])
    api.planStatusAll.mockResolvedValue([
      { credential_owner: 'u1', user_id: 'u1', status: 'rejected', held: true, hold_until: iso(2), resets_at: iso(2), banner: { kind: 'limit' } },
      { credential_owner: 'u2', user_id: 'u2', status: 'allowed_warning', held: false, banner: { kind: 'warning' } },
      { credential_owner: 'u3', user_id: 'u3', status: 'rejected', held: false, api_key: true, banner: null },
      { credential_owner: 'org', user_id: null, status: 'allowed', held: false, api_key: true, banner: null },
    ])
    view = await render(<UsersView currentUser={{ id: 'x' }} />)
    await flush()
    const rows = [...view.container.querySelectorAll('tbody tr')]
    expect(rows[0].textContent).toContain('Org fallback key')
    const byName = (n) => rows.find((r) => r.textContent.includes(n))
    expect(byName('Asha').textContent).toMatch(/Limit reached until (tomorrow |\d+ \w+, )?\d{2}:\d{2}/)
    expect(byName('Bea').textContent).toContain('Close to limit')
    expect(byName('Cy').textContent).not.toMatch(/Limit reached|Close to limit|Try now/)
    expect(byName('Bea').textContent).not.toContain('Try now')
    const btn = [...byName('Asha').querySelectorAll('button')].find((b) => b.textContent === 'Try now')
    await act(async () => btn.dispatchEvent(new MouseEvent('click', { bubbles: true })))
    expect(document.body.textContent).toContain("Let Asha's work start now? If the limit is still reached, it will pause again.")
    const ok = [...document.body.querySelectorAll('.ant-popover button')].find((b) => /^(OK|Yes)$/i.test(b.textContent.trim()))
    const before = api.planStatusAll.mock.calls.length
    await act(async () => ok.dispatchEvent(new MouseEvent('click', { bubbles: true })))
    await flush()
    expect(api.planTryNow).toHaveBeenCalledWith('u1')
    expect(api.planStatusAll.mock.calls.length).toBeGreaterThan(before)
  })
})
