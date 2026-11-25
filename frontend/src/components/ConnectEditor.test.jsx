import { act } from 'react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { flush, render } from '../home/testRender'

vi.mock('../api', () => ({
  api: {
    myEditorKeys: vi.fn(),
    createEditorKey: vi.fn(),
    revokeEditorKey: vi.fn(),
    listProjects: vi.fn(),
  },
}))

import { api } from '../api'
import ConnectEditor from './ConnectEditor'

const q = (id) => document.querySelector(`[data-testid="${id}"]`)
const click = (el) => act(async () => { el.dispatchEvent(new MouseEvent('click', { bubbles: true })) })
const type = async (el, value) => {
  await act(async () => {
    const set = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set
    set.call(el, value)
    el.dispatchEvent(new Event('input', { bubbles: true }))
  })
}
const KEY = 'coe_SECRETSECRETSECRETSECRET1234567890'
const KEYS = { enabled: true, max_keys: 5, days: [30, 90, 365], keys: [
  { id: 1, name: 'Laptop', prefix: 'coe_ab12', created_at: null, expires_at: '2027-02-01T00:00:00', last_used_at: null, use_count: 0, revoked_at: null, projects: null, project_names: [], expired: false },
  { id: 2, name: 'Old', prefix: 'coe_zz99', created_at: null, expires_at: '2026-01-01T00:00:00', last_used_at: null, use_count: 0, revoked_at: null, projects: null, project_names: [], expired: true },
] }

let view
const el = (open) => <ConnectEditor open={open} onClose={() => {}} />
async function mount(open = true) {
  view = await render(el(open))
  await flush()
  await flush()
}

beforeAll(() => {
  window.matchMedia ||= () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} })
})
beforeEach(() => {
  vi.clearAllMocks()
  api.myEditorKeys.mockImplementation(async () => KEYS)
  api.listProjects.mockImplementation(async () => [{ id: 3, name: 'Alpha' }])
  api.createEditorKey.mockImplementation(async () => ({ id: 9, name: 'N', prefix: 'coe_SECR', key: KEY, url: 'https://co.example', expires_at: '2027-01-01', projects: null }))
  api.revokeEditorKey.mockImplementation(async () => null)
})
afterEach(async () => { await view?.unmount?.(); view = null })

describe('ConnectEditor', () => {
  it('shows the intro, keys and tags', async () => {
    await mount()
    const t = document.body.textContent
    expect(t).toContain('Let Claude Code or Cursor on your own computer read your team')
    expect(t).toContain('starts with coe_ab12…')
    expect(t).toContain('Every project you can see')
    expect(t).toContain('Expired')
  })

  it('says when an admin has not turned it on', async () => {
    api.myEditorKeys.mockImplementation(async () => ({ ...KEYS, enabled: false }))
    await mount()
    expect(document.body.textContent).toContain("An admin hasn't turned on editor connections.")
    expect(q('ce-create')).toBeNull()
    expect(document.body.textContent).toContain('Laptop')
  })

  it('creates a key, shows it once with setup, and forgets it after close', async () => {
    await mount()
    await type(q('ce-name'), 'N')
    await click(q('ce-create'))
    await flush()
    expect(api.createEditorKey).toHaveBeenCalledWith({ name: 'N', days: 90 })
    expect(q('ce-key').value).toBe(KEY)
    expect(document.body.textContent).toContain("You won't see this key again.")
    expect(q('ce-claude').textContent).toBe(`claude mcp add --transport http team-decisions https://co.example/mcp --header "Authorization: Bearer ${KEY}"`)
    await view.rerender(el(false))
    await flush()
    expect(document.body.textContent).not.toContain(KEY)
    await view.rerender(el(true))
    await flush()
    await flush()
    expect(document.body.textContent).not.toContain(KEY)
    expect(q('ce-created')).toBeNull()
  })

  it('shows the server message plainly when creating fails', async () => {
    api.createEditorKey.mockImplementation(async () => { throw new Error('Editor connections are turned off by an admin.') })
    await mount()
    await type(q('ce-name'), 'N')
    await click(q('ce-create'))
    await flush()
    expect(q('ce-error').textContent).toContain('turned off by an admin')
  })

  it('turns a key off after confirming', async () => {
    await mount()
    await click(q('ce-off-1'))
    expect(document.body.textContent).toContain('Turn off this key? Editors using it stop working right away.')
    await click([...document.querySelectorAll('.ant-popconfirm .ant-btn-primary')].pop())
    await flush()
    expect(api.revokeEditorKey).toHaveBeenCalledWith(1)
  })
})
