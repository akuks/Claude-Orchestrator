import { act } from 'react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { flush, render } from '../home/testRender'

vi.mock('../api', () => ({
  api: {
    listGuardRules: vi.fn(async () => []),
    guardBuiltins: vi.fn(),
    guardOrgSettings: vi.fn(),
    saveGuardOrgSettings: vi.fn(),
    guardHealth: vi.fn(async () => ({ hook_errors: { count: 0, window_hours: 48 } })),
  },
}))

import { api } from '../api'
import OrgGuardView, { GroundRulesCard, ModelTiersCard } from './OrgGuardView'

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
  api.guardHealth.mockImplementation(async () => ({ hook_errors: { count: 0, window_hours: 48 } }))
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

  it('saves the ground rules with all eight keys and shows the 400', async () => {
    api.guardOrgSettings.mockImplementation(async () => ({
      disabled_rules: [], dev_db_patterns: [],
      ground_rules: { grounding: true, short_answers: true, usage_care: true, notes: true, questions: true, decisions: true, local_first: true, extra: '' },
    }))
    api.saveGuardOrgSettings.mockImplementation(async (b) => b)
    const c = await mount({ isAdmin: true })
    await click(q(c, 'ground-short_answers'))
    await click(q(c, 'ground-rules-save'))
    await flush()
    expect(api.saveGuardOrgSettings).toHaveBeenCalledWith({
      ground_rules: { grounding: true, short_answers: false, usage_care: true, notes: true, questions: true, decisions: true, local_first: true, extra: '' },
    })
    api.saveGuardOrgSettings.mockImplementation(async () => {
      throw Object.assign(new Error('extra is at most 2000 characters'), { status: 400 })
    })
    await click(q(c, 'ground-notes'))
    await click(q(c, 'ground-rules-save'))
    await flush()
    expect(q(c, 'ground-rules-error').textContent).toContain('extra is at most 2000 characters')
  })

  it('keeps decisions off when saving another switch', async () => {
    api.guardOrgSettings.mockImplementation(async () => ({
      disabled_rules: [], dev_db_patterns: [],
      ground_rules: { grounding: true, short_answers: true, usage_care: true, notes: true, questions: true, decisions: false, local_first: true, extra: '' },
    }))
    api.saveGuardOrgSettings.mockImplementation(async (b) => b)
    const c = await mount({ isAdmin: true })
    await click(q(c, 'ground-notes'))
    await click(q(c, 'ground-rules-save'))
    await flush()
    expect(api.saveGuardOrgSettings).toHaveBeenCalledWith({
      ground_rules: { grounding: true, short_answers: true, usage_care: true, notes: false, questions: true, decisions: false, local_first: true, extra: '' },
    })
  })

  it('a parent reload with the same ground rules keeps unsaved edits', async () => {
    const rules = { grounding: true, short_answers: true, usage_care: true, notes: true, questions: true, decisions: true, local_first: true, extra: '' }
    view = await render(<GroundRulesCard canEdit initial={{ ...rules }} />)
    const c = view.container
    await click(q(c, 'ground-notes'))
    expect(q(c, 'ground-rules-save').disabled).toBe(false)
    await view.rerender(<GroundRulesCard canEdit initial={{ ...rules }} />) // a new object, the same values
    expect(q(c, 'ground-rules-save').disabled).toBe(false)
    await view.rerender(<GroundRulesCard canEdit initial={{ ...rules, notes: false }} />) // new content resets
    expect(q(c, 'ground-rules-save').disabled).toBe(true)
  })

  it('ground rules are read-only for non-admins', async () => {
    const c = await mount({ isAdmin: false })
    expect(q(c, 'ground-rules-save')).toBeNull()
    expect(q(c, 'ground-grounding').disabled).toBe(true)
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

  it('shows the hook-error health line when hooks failed lately', async () => {
    api.guardHealth.mockImplementation(async () => ({
      hook_errors: { count: 3, window_hours: 48, latest_hook_event: 'SessionEnd', latest_error: 'OSError: disk full' },
    }))
    const c = await mount({ isAdmin: true })
    expect(q(c, 'guard-health').textContent).toContain(
      '3 co-guard hook errors in the last 48 h; latest (SessionEnd): OSError: disk full',
    )
  })

  it('no health line when there were no hook errors, or the check failed', async () => {
    let c = await mount({ isAdmin: true })
    expect(q(c, 'guard-health')).toBeNull()
    await view.unmount()
    api.guardHealth.mockImplementation(async () => {
      throw new Error('nope')
    })
    c = await mount({ isAdmin: true })
    expect(q(c, 'guard-health')).toBeNull()
    expect(q(c, 'builtin-list')).not.toBeNull()
  })

  describe('Model tiers card', () => {
    const TIERS = { strongest: 'opus', standard: 'sonnet', light: 'claude-haiku-4-5' }
    const withTiers = () =>
      api.guardOrgSettings.mockImplementation(async () => ({ disabled_rules: [], dev_db_patterns: [], model_tiers: TIERS }))

    it('shows three rows from model_tiers', async () => {
      withTiers()
      const c = await mount({ isAdmin: true })
      const card = q(c, 'model-tiers')
      expect(card.textContent).toContain('Model tiers')
      for (const k of ['strongest', 'standard', 'light']) expect(q(c, `tier-${k}`)).not.toBeNull()
      expect(q(c, 'tier-light').querySelector('input').value).toBe('claude-haiku-4-5')
    })

    it('admin Save sends the whole map', async () => {
      withTiers()
      api.saveGuardOrgSettings.mockImplementation(async (b) => b)
      view = await render(<ModelTiersCard canEdit initial={TIERS} />)
      const c = view.container
      const input = q(c, 'tier-strongest').querySelector('input')
      await act(async () => {
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, 'claude-opus-4-1')
        input.dispatchEvent(new Event('input', { bubbles: true }))
      })
      await click(q(c, 'model-tiers-save'))
      await flush()
      expect(api.saveGuardOrgSettings).toHaveBeenCalledWith({
        model_tiers: { strongest: 'claude-opus-4-1', standard: 'sonnet', light: 'claude-haiku-4-5' },
      })
    })

    it('non-admin sees read-only values', async () => {
      withTiers()
      const c = await mount({ isAdmin: false })
      expect(q(c, 'model-tiers-save')).toBeNull()
      expect(q(c, 'tier-strongest').querySelector('input')).toBeNull()
      expect(q(c, 'tier-strongest').textContent).toContain('opus')
    })

    it('shows a 400 message', async () => {
      api.saveGuardOrgSettings.mockImplementation(async () => {
        throw Object.assign(new Error('model_tiers.light must be opus, sonnet, haiku or a claude-… model id'), { status: 400 })
      })
      view = await render(<ModelTiersCard canEdit initial={TIERS} />)
      const c = view.container
      const input = q(c, 'tier-light').querySelector('input')
      await act(async () => {
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, 'claude-xyz')
        input.dispatchEvent(new Event('input', { bubbles: true }))
      })
      await click(q(c, 'model-tiers-save'))
      await flush()
      expect(q(c, 'model-tiers-error').textContent).toContain('model_tiers.light must be')
    })
  })
})
