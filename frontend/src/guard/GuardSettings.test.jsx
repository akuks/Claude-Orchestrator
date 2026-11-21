import { act } from 'react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { flush, render } from '../home/testRender'

vi.mock('../api', () => ({
  api: {
    updateProject: vi.fn(async (id, v) => ({ id, ...v })),
    listGuardRules: vi.fn(async () => []),
    listProjects: vi.fn(async () => [
      { id: 'p1', name: 'P' },
      { id: 'p2', name: 'Legacy app' },
      { id: 'p3', name: 'Shared lib' },
    ]),
    projectRulesFile: vi.fn(async () => ({ path: '/w/.claude-orchestrator/rules.md', present: false, read: true, confirm: [], never: [], errors: [] })),
  },
}))

import { api } from '../api'
import GuardSettings from './GuardSettings'

const project = (over = {}) => ({ id: 'p1', name: 'P', guard_enabled: true, approval_wait_seconds: 300, my_role: 'maintainer', ...over })
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
// antd Select: open it, then pick the option with this label.
async function pick(wrapper, label) {
  await act(async () => {
    wrapper.querySelector('.ant-select-selector').dispatchEvent(new MouseEvent('mousedown', { bubbles: true }))
  })
  await flush()
  const opt = [...document.querySelectorAll('.ant-select-item-option')].find((o) => o.textContent === label)
  await click(opt)
}

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

  it('saves only the policy fields that changed (trust, quarantine, no-answer, read-only paths, references)', async () => {
    const onSaved = vi.fn()
    const c = await mount({
      project: project({ readonly_paths: [{ path: 'vendor', reason: '' }], reference_project_ids: ['p9'] }),
      canEdit: true,
      onSaved,
    })
    await pick(q(c, 'guard-trust'), "External (someone else's code)")
    await click(q(c, 'guard-quarantine'))
    await pick(q(c, 'guard-no-answer'), 'Block, stop, resume on approval')
    expect(q(c, 'guard-no-answer-help').textContent).toMatch(/24 h/)
    await click(q(c, 'guard-readonly-add'))
    const inputs = q(c, 'guard-readonly-paths').querySelectorAll('input')
    await type(inputs[2], ' legacy ')
    await type(inputs[3], 'old code')
    await pick(q(c, 'guard-references'), 'Legacy app')
    await click(btn(c, 'Save guard settings'))
    await flush()
    expect(api.updateProject).toHaveBeenCalledWith('p1', {
      guard_enabled: true,
      approval_wait_seconds: 300,
      trust: 'external',
      quarantine: true,
      on_no_answer: 'deny_and_resume',
      readonly_paths: [{ path: 'vendor' }, { path: 'legacy', reason: 'old code' }],
      reference_project_ids: ['p9', 'p2'],
    })
    expect(onSaved).toHaveBeenCalled()
  })

  it('a stored reference the editor cannot see is named as such, and an unchanged list is not sent', async () => {
    const c = await mount({ project: project({ reference_project_ids: ['p9'] }), canEdit: true })
    expect(q(c, 'guard-references').textContent).toContain('a project you cannot see')
    await click(q(c, 'guard-quarantine'))
    await click(btn(c, 'Save guard settings'))
    await flush()
    expect(api.updateProject).toHaveBeenCalledWith('p1', { guard_enabled: true, approval_wait_seconds: 300, quarantine: true })
  })

  it('shows the server error (e.g. a bad read-only path)', async () => {
    api.updateProject.mockImplementationOnce(async () => {
      throw Object.assign(new Error("read-only path '/' is / or your home folder or above it"), { status: 400 })
    })
    const c = await mount({ project: project(), canEdit: true })
    await click(q(c, 'guard-readonly-add'))
    await type(q(c, 'guard-readonly-paths').querySelector('input'), '/')
    await click(btn(c, 'Save guard settings'))
    await flush()
    expect(q(c, 'guard-save-error').textContent).toContain('is / or your home folder')
  })

  it('shows the trust hint, the read-only hint and the trust reason when known', async () => {
    const c = await mount({ project: project({ trust_reason: 'an org member has commits in this repo' }), canEdit: true })
    expect(q(c, 'guard-trust-reason').textContent).toContain('an org member has commits')
    expect(q(c, 'guard-trust-help').textContent).toMatch(/commit authors' emails/)
    expect(q(c, 'guard-readonly-help').textContent).toMatch(/name the folders to change instead, e\.g\. src/)
    expect(q(c, 'rule-table-project')).not.toBeNull()
    expect(q(c, 'rule-table-org')).not.toBeNull()
    expect(api.listGuardRules).toHaveBeenCalledWith('p1')
    expect(api.listGuardRules).toHaveBeenCalledWith(undefined)
  })

  it('repo rules file: read rules, errors with line numbers, and not read for external projects', async () => {
    api.projectRulesFile.mockImplementationOnce(async () => ({
      path: '/w/.claude-orchestrator/rules.md', present: true, read: true,
      confirm: [{ id: 'repo.x', kind: 'confirm', match: 'text', pattern: 'make deploy', reason: 'ships', line: 3 }],
      never: [],
      errors: [{ line: 7, message: 'disable: lines are ignored; only an admin can switch rules off' }],
    }))
    let c = await mount({ project: project(), canEdit: true })
    expect(q(c, 'rules-file-count').textContent).toBe('Read: 1 rule')
    expect(q(c, 'rules-file').textContent).toContain('make deploy')
    expect(q(c, 'rules-file-errors').textContent).toContain('line 7: disable: lines are ignored')
    await view.unmount()
    api.projectRulesFile.mockImplementationOnce(async () => ({ path: '/w/r.md', present: true, read: false, confirm: [], never: [], errors: [] }))
    c = await mount({ project: project({ trust: 'external' }), canEdit: false })
    expect(q(c, 'rules-file-not-read').textContent).toMatch(/Not read: this project is external \(the file exists\)/)
  })
})
