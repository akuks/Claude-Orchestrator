import { act } from 'react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { flush, render } from '../home/testRender'

vi.mock('../api', () => ({
  api: {
    listGuardRules: vi.fn(async () => []),
    guardBuiltins: vi.fn(),
    guardOrgSettings: vi.fn(),
    saveGuardOrgSettings: vi.fn(),
  },
}))

import { api } from '../api'
import OrgGuardView from './OrgGuardView'

const BUILTINS = [
  { id: 'rm-rf-outside', reason: 'this deletes a folder outside the project', hard_reason: 'this would delete your home folder', disableable: true },
  { id: 'git-reset-hard', reason: 'this throws away your uncommitted work', hard_reason: null, disableable: true },
  { id: 'self-protect', reason: null, hard_reason: "this changes co-guard's own files", disableable: false },
  { id: 'readonly-source', reason: null, hard_reason: 'this changes a read-only source', disableable: false },
]
const q = (c, id) => c.querySelector(`[data-testid="${id}"]`)
const click = (el) => act(async () => { el.dispatchEvent(new MouseEvent('click', { bubbles: true })) })

let view
async function mount(props) {
  view = await render(<OrgGuardView {...props} />)
  await flush()
  await flush()
  return view.container
}

beforeAll(() => {
  window.matchMedia ||= () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} })
})
beforeEach(() => {
  vi.clearAllMocks()
  api.guardBuiltins.mockImplementation(async () => BUILTINS)
  api.guardOrgSettings.mockImplementation(async () => ({ disabled_rules: ['git-reset-hard'], dev_db_patterns: ['host:*.dev'] }))
})
afterEach(async () => {
  await view?.unmount()
  view = null
})

describe('OrgGuardView (Settings → Agent guard)', () => {
  it('shows switchable built-ins as checkboxes and the rest locked with why', async () => {
    const c = await mount({ isAdmin: true })
    const reset = q(c, 'builtin-git-reset-hard').querySelector('input[type="checkbox"]')
    expect(reset.checked).toBe(false) // switched off
    expect(reset.disabled).toBe(false)
    const locked = q(c, 'builtin-self-protect')
    expect(locked.querySelector('input[type="checkbox"]').disabled).toBe(true)
    expect(locked.querySelector('input[type="checkbox"]').checked).toBe(true)
    expect(locked.textContent).toContain('locked')
    expect(locked.textContent).toContain("this changes co-guard's own files")
    expect(q(c, 'builtin-readonly-source').querySelector('input').disabled).toBe(true)
    expect(c.textContent).toMatch(/cannot be switched off/)
    expect(q(c, 'rm-rf-warning').textContent).toMatch(/rm -rf on \.git and on the project folder/)
  })

  it('saves disabled built-ins and dev patterns', async () => {
    api.saveGuardOrgSettings.mockImplementation(async (b) => b)
    const c = await mount({ isAdmin: true })
    const save = q(c, 'org-guard-save')
    expect(save.disabled).toBe(true)
    await click(q(c, 'builtin-rm-rf-outside').querySelector('input'))
    await click(q(c, 'builtin-git-reset-hard').querySelector('input'))
    expect(save.disabled).toBe(false)
    await click(save)
    await flush()
    expect(api.saveGuardOrgSettings).toHaveBeenCalledWith({ disabled_rules: ['rm-rf-outside'], dev_db_patterns: ['host:*.dev'] })
  })

  it("shows the server's refusal", async () => {
    api.saveGuardOrgSettings.mockImplementation(async () => {
      throw Object.assign(new Error('"*" matches every database; name the dev hosts or servers'), { status: 400 })
    })
    const c = await mount({ isAdmin: true })
    await click(q(c, 'builtin-git-reset-hard').querySelector('input'))
    await click(q(c, 'org-guard-save'))
    await flush()
    expect(q(c, 'org-guard-error').textContent).toContain('matches every database')
  })

  it('has dev pattern syntax help', async () => {
    const c = await mount({ isAdmin: true })
    expect(q(c, 'dev-db-help').textContent).toMatch(/host:<pattern>/)
    expect(q(c, 'dev-db-help').textContent).toMatch(/mcp:<prefix>\*/)
    expect(c.textContent).toContain('host:*.dev')
  })

  it('is read-only for non-admins', async () => {
    const c = await mount({ isAdmin: false })
    expect(q(c, 'org-guard-save')).toBeNull()
    expect(q(c, 'builtin-git-reset-hard').querySelector('input').disabled).toBe(true)
    expect(q(c, 'rule-add-org')).toBeNull()
  })
})
