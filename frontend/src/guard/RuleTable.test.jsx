import { act } from 'react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { flush, render } from '../home/testRender'

vi.mock('../api', () => ({
  api: {
    listGuardRules: vi.fn(),
    createGuardRule: vi.fn(),
    updateGuardRule: vi.fn(),
    deleteGuardRule: vi.fn(),
  },
}))

import { api } from '../api'
import RuleTable, { checkRule } from './RuleTable'

const rule = (over = {}) => ({
  id: 'x1',
  rule_id: 'org.kubectl-delete',
  scope: 'org',
  project_id: null,
  slug: 'kubectl-delete',
  kind: 'confirm',
  match: 'text',
  pattern: 'kubectl delete',
  reason: 'cluster changes need a person',
  enabled: true,
  ...over,
})
const q = (c, id) => c.querySelector(`[data-testid="${id}"]`)
const btn = (c, label) => [...c.querySelectorAll('button')].find((b) => b.textContent.trim() === label)
const click = (el) => act(async () => { el.dispatchEvent(new MouseEvent('click', { bubbles: true })) })
async function type(el, value) {
  await act(async () => {
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set
    setter.call(el, value)
    el.dispatchEvent(new Event('input', { bubbles: true }))
  })
}

let view
async function mount(props) {
  view = await render(<RuleTable {...props} />)
  await flush()
  await flush()
  return view.container
}

beforeAll(() => {
  window.matchMedia ||= () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} })
})
beforeEach(() => {
  vi.clearAllMocks()
  api.listGuardRules.mockImplementation(async () => [rule()])
})
afterEach(async () => {
  await view?.unmount()
  view = null
  document.body.innerHTML = ''
})

describe('checkRule', () => {
  it('mirrors the server limits', () => {
    expect(checkRule({ pattern: '', match: 'text' })).toMatch(/required/)
    expect(checkRule({ pattern: 'x'.repeat(501), match: 'text' })).toMatch(/500/)
    expect(checkRule({ pattern: 'a\tb', match: 'text' })).toMatch(/one line/)
    expect(checkRule({ pattern: 'ok', match: 'regex' })).toBeNull()
  })
})

describe('RuleTable', () => {
  it('lists rules as plain text with the seatbelt hint', async () => {
    api.listGuardRules.mockImplementation(async () => [rule({ pattern: '<img src=x onerror=alert(1)>' })])
    const c = await mount({ scope: 'org', canEdit: true })
    expect(api.listGuardRules).toHaveBeenCalledWith(undefined)
    expect(c.textContent).toContain('org.kubectl-delete')
    expect(c.textContent).toContain('<img src=x onerror=alert(1)>')
    expect(c.querySelector('img')).toBeNull()
    expect(q(c, 'rule-seatbelt').textContent).toMatch(/seatbelt, not a boundary/)
    expect(q(c, 'rule-seatbelt').textContent).toMatch(/reading \.env/)
  })

  it('project scope loads that project and is read-only without canEdit', async () => {
    const c = await mount({ scope: 'project', projectId: 'p1', canEdit: false })
    expect(api.listGuardRules).toHaveBeenCalledWith('p1')
    expect(q(c, 'rule-add-project')).toBeNull()
    expect(q(c, 'rule-enabled-x1').disabled).toBe(true)
  })

  it('shows the server validation error in the dialog and keeps it open', async () => {
    api.createGuardRule.mockImplementation(async () => {
      throw Object.assign(new Error('invalid regex: missing ), unterminated subpattern at position 0'), { status: 400 })
    })
    const c = await mount({ scope: 'project', projectId: 'p1', canEdit: true })
    await click(q(c, 'rule-add-project'))
    await flush()
    await click(document.body.querySelector('input[value="regex"]'))
    await type(document.body.querySelector('[data-testid="rule-pattern"]'), '(')
    await type(document.body.querySelector('[data-testid="rule-reason"]'), 'why')
    await click(document.body.querySelector('[data-testid="rule-save"]'))
    await flush()
    expect(api.createGuardRule).toHaveBeenCalledWith({
      kind: 'confirm',
      match: 'regex',
      pattern: '(',
      reason: 'why',
      enabled: true,
      scope: 'project',
      project_id: 'p1',
    })
    expect(document.body.querySelector('[data-testid="rule-error"]').textContent).toContain('invalid regex: missing )')
  })

  it('a local check stops an empty pattern before the server', async () => {
    const c = await mount({ scope: 'org', canEdit: true })
    await click(q(c, 'rule-add-org'))
    await flush()
    await click(document.body.querySelector('[data-testid="rule-save"]'))
    await flush()
    expect(api.createGuardRule).not.toHaveBeenCalled()
    expect(document.body.querySelector('[data-testid="rule-error"]').textContent).toMatch(/pattern is required/)
  })

  it('adds an org rule (never kind, with a name) and reloads', async () => {
    api.createGuardRule.mockImplementation(async (b) => rule(b))
    const c = await mount({ scope: 'org', canEdit: true })
    await click(q(c, 'rule-add-org'))
    await flush()
    await click(document.body.querySelector('input[value="never"]'))
    await type(document.body.querySelector('[data-testid="rule-name"]'), 'no prod')
    await type(document.body.querySelector('[data-testid="rule-pattern"]'), 'deploy --prod')
    await click(document.body.querySelector('[data-testid="rule-save"]'))
    await flush()
    expect(api.createGuardRule).toHaveBeenCalledWith({
      kind: 'never', match: 'text', pattern: 'deploy --prod', reason: '', enabled: true, scope: 'org', name: 'no prod',
    })
    expect(api.listGuardRules).toHaveBeenCalledTimes(2)
  })

  it('edits, toggles and deletes', async () => {
    api.updateGuardRule.mockImplementation(async () => rule())
    api.deleteGuardRule.mockImplementation(async () => null)
    const c = await mount({ scope: 'org', canEdit: true })
    await click(q(c, 'rule-enabled-x1'))
    await flush()
    expect(api.updateGuardRule).toHaveBeenCalledWith('x1', { enabled: false })

    await click(c.querySelector('button[aria-label="Edit org.kubectl-delete"]'))
    await flush()
    const pat = document.body.querySelector('[data-testid="rule-pattern"]')
    expect(pat.value).toBe('kubectl delete')
    await type(pat, 'kubectl delete ns')
    await click(document.body.querySelector('[data-testid="rule-save"]'))
    await flush()
    expect(api.updateGuardRule).toHaveBeenLastCalledWith('x1', {
      kind: 'confirm', match: 'text', pattern: 'kubectl delete ns', reason: 'cluster changes need a person', enabled: true,
    })

    await click(c.querySelector('button[aria-label="Delete org.kubectl-delete"]'))
    await flush()
    await click(btn(document.body, 'Delete'))
    await flush()
    expect(api.deleteGuardRule).toHaveBeenCalledWith('x1')
  })
})
