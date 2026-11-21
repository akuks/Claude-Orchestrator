import { act } from 'react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { flush, render } from '../home/testRender'

vi.mock('../api', () => ({ api: { listGrants: vi.fn(), revokeGrant: vi.fn() } }))

import { api } from '../api'
import RunGrants from './RunGrants'

const grant = (over = {}) => ({
  id: 'g1', root_id: 'root1', task_id: 't1', scope: 'run', rule_id: 'git-reset-hard', key_kind: 'command', key: 'a'.repeat(64),
  expires_at: '2099-01-01T00:00:00Z', created_at: '2026-11-21T10:00:00Z', use_count: 2, active: true, can_revoke: true, ...over,
})
const q = (c, id) => c.querySelector(`[data-testid="${id}"]`)
const btn = (c, label) => [...c.querySelectorAll('button')].find((b) => b.textContent.trim() === label)
const click = (el) => act(async () => { el.dispatchEvent(new MouseEvent('click', { bubbles: true })) })

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

describe('RunGrants', () => {
  it('lists live grants with what they cover and the running-step note', async () => {
    api.listGrants.mockImplementation(async () => [
      grant(),
      grant({ id: 'g2', rule_id: 'guest-quarantine', key_kind: 'rule', key: '*', can_revoke: false }),
    ])
    view = await render(<RunGrants />)
    await flush()
    const c = view.container
    expect(api.listGrants).toHaveBeenCalledWith(undefined)
    expect(c.textContent).toContain('Run and follow-up grants (2)')
    expect(c.textContent).toContain('the same command')
    expect(c.textContent).toContain("all of this repo's code")
    expect(c.querySelector('a[href="#/tasks/root1"]')).not.toBeNull()
    expect(q(c, 'run-grants-note').textContent).toMatch(/running step keeps its grants until it ends/)
    expect(q(c, 'grant-revoke-g1')).not.toBeNull()
    expect(q(c, 'grant-revoke-g2')).toBeNull() // not allowed to revoke
  })

  it('revokes after a confirmation and reloads', async () => {
    api.listGrants.mockImplementationOnce(async () => [grant()]).mockImplementation(async () => [])
    api.revokeGrant.mockImplementation(async () => grant({ active: false }))
    view = await render(<RunGrants />)
    await flush()
    await click(q(view.container, 'grant-revoke-g1'))
    await flush()
    expect(api.revokeGrant).not.toHaveBeenCalled()
    await click(btn(document.querySelector('.ant-popconfirm'), 'Revoke'))
    await flush()
    expect(api.revokeGrant).toHaveBeenCalledWith('g1')
    expect(view.container.textContent).toContain('Run and follow-up grants (0)')
  })

  it('fix round 1: a tool key is shown with hidden characters revealed', async () => {
    api.listGrants.mockImplementation(async () => [grant({ rule_id: 'mcp-require-approval', key_kind: 'tool', key: 'mcp__db\u202e__q' })])
    view = await render(<RunGrants />)
    await flush()
    expect(view.container.textContent).toContain('every call of the tool mcp__db⟦U+202E⟧__q')
  })

  it('fix round 1: nothing reloads or sets state after unmount', async () => {
    api.listGrants.mockImplementation(async () => [grant()])
    let finish
    api.revokeGrant.mockImplementation(() => new Promise((r) => { finish = r }))
    const err = vi.spyOn(console, 'error').mockImplementation(() => {})
    view = await render(<RunGrants />)
    await flush()
    await click(q(view.container, 'grant-revoke-g1'))
    await flush()
    await click(btn(document.querySelector('.ant-popconfirm'), 'Revoke'))
    const loads = api.listGrants.mock.calls.length
    await view.unmount()
    view = null
    await act(async () => finish(grant({ active: false })))
    expect(api.listGrants.mock.calls.length).toBe(loads) // no reload once unmounted
    const calls = err.mock.calls.map((c) => String(c[0])).filter((m) => !m.includes('Not implemented'))
    err.mockRestore()
    expect(calls).toEqual([])
  })
})
