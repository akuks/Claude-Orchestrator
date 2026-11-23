import { act } from 'react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { flush, render } from '../home/testRender'

vi.mock('../api', () => ({
  api: {
    listDecisions: vi.fn(),
    decisionsHealth: vi.fn(),
    createDecision: vi.fn(),
    updateDecision: vi.fn(),
    verifyDecision: vi.fn(),
    retireDecision: vi.fn(),
    supersedeDecision: vi.fn(),
  },
}))
import { api } from '../api'
import DecisionsPane from './DecisionsPane'
import { renderLine } from '../components/TaskDetail'
import { GROUND_SWITCHES } from '../guard/OrgGuardView'

const dec = (n, over = {}) => ({
  id: `i${n}`, number: n, display_id: `D-${String(n).padStart(3, '0')}`, kind: 'decision', title: `title ${n}`, why: 'because',
  see: null, touches: ['src/a.js'], decided_by: 'u', decided_by_name: 'Ann', decided_at: '2026-11-20T10:00:00Z',
  last_verified_at: '2026-11-21T10:00:00Z', source: '', status: 'active', superseded_by: null, version: 3, ...over,
})
const project = { id: 'p1', name: 'P' }
const q = (c, id) => c.querySelector(`[data-testid="${id}"]`)
const click = (el) => act(async () => { el.dispatchEvent(new MouseEvent('click', { bubbles: true })) })
async function type(el, value) {
  await act(async () => {
    const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype
    Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, value)
    el.dispatchEvent(new Event('input', { bubbles: true }))
  })
}
let view
async function mount(props = {}) {
  view = await render(<DecisionsPane project={project} canEdit {...props} />)
  await flush()
  return view.container
}
beforeAll(() => {
  window.matchMedia ||= () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} })
})
beforeEach(() => {
  vi.clearAllMocks()
  api.listDecisions.mockResolvedValue([dec(12, { kind: 'rule' }), dec(15)])
  api.decisionsHealth.mockResolvedValue({ available: true, git_error: null, ok: true, items: [] })
  for (const k of ['verifyDecision', 'retireDecision', 'supersedeDecision', 'createDecision', 'updateDecision']) api[k].mockResolvedValue({})
})
afterEach(async () => {
  await view?.unmount()
  view = null
})

describe('DecisionsPane', () => {
  it('lists id, kind tag and touches chips; filters reload', async () => {
    const c = await mount()
    expect(q(c, 'decision-12').textContent).toContain('D-012')
    expect(q(c, 'decision-12').textContent).toContain('rule')
    expect(q(c, 'decision-12').textContent).toContain('src/a.js')
    expect(api.listDecisions).toHaveBeenLastCalledWith('p1', { status: 'active', kind: '' })
    await click([...c.querySelectorAll('.ant-radio-button-wrapper input')].find((i) => i.value === 'all'))
    await flush()
    expect(api.listDecisions).toHaveBeenLastCalledWith('p1', { status: 'all', kind: '' })
  })

  it('a viewer sees no write buttons', async () => {
    const c = await mount({ canEdit: false })
    expect(q(c, 'decision-12')).not.toBeNull()
    expect(q(c, 'decision-add')).toBeNull()
    expect(q(c, 'verify-12')).toBeNull()
    expect(q(c, 'rule-12')).toBeNull()
  })

  it('verify and retire call the routes; supersede too', async () => {
    const c = await mount()
    await click(q(c, 'verify-12'))
    expect(api.verifyDecision).toHaveBeenCalledWith('p1', 12)
    await click(q(c, 'retire-12'))
    await click([...document.querySelectorAll('.ant-popover button')].find((b) => /Retire/.test(b.textContent)))
    expect(api.retireDecision).toHaveBeenCalledWith('p1', 12)
    await act(async () => {
      q(c, 'supersede-12').querySelector('.ant-select-selector').dispatchEvent(new MouseEvent('mousedown', { bubbles: true }))
    })
    await flush()
    await click([...document.querySelectorAll('.ant-select-item-option')].find((o) => o.textContent.includes('D-015')))
    expect(api.supersedeDecision).toHaveBeenCalledWith('p1', 12, 15)
  })

  it('Make this a rule hands over a draft', async () => {
    const onMakeRule = vi.fn()
    const c = await mount({ onMakeRule })
    await click(q(c, 'rule-12'))
    expect(onMakeRule).toHaveBeenCalledWith({ kind: 'never', match: 'path', pattern: 'src/a.js', reason: 'D-012 title 12' })
  })

  it('add: a reference needs see; a server 422 is shown', async () => {
    const c = await mount()
    await click(q(c, 'decision-add'))
    await type(document.querySelector('[data-testid="decision-title"]'), 'My ref')
    await act(async () => {
      document.querySelector('[data-testid="decision-kind"] .ant-select-selector').dispatchEvent(new MouseEvent('mousedown', { bubbles: true }))
    })
    await flush()
    await click([...document.querySelectorAll('.ant-select-item-option')].find((o) => o.textContent === 'reference'))
    await click(document.querySelector('[data-testid="decision-save"]'))
    expect(document.querySelector('[data-testid="decision-error"]').textContent).toContain('needs a see')
    expect(api.createDecision).not.toHaveBeenCalled()
    await type(document.querySelector('[data-testid="decision-see"]'), 'docs/x.md')
    api.createDecision.mockRejectedValueOnce(Object.assign(new Error('see: file does not exist'), { status: 422 }))
    await click(document.querySelector('[data-testid="decision-save"]'))
    expect(document.querySelector('[data-testid="decision-error"]').textContent).toContain('file does not exist')
    expect(api.createDecision.mock.calls[0][1]).toMatchObject({ kind: 'reference', title: 'My ref', see: 'docs/x.md' })
  })

  it('edit 409 reloads and shows the notice', async () => {
    const c = await mount()
    const [, calls] = [0, api.listDecisions.mock.calls.length]
    const edit = [...q(c, 'decision-12').querySelectorAll('button')].find((b) => b.textContent === 'Edit')
    await click(edit)
    api.updateDecision.mockRejectedValueOnce(Object.assign(new Error('changed by someone else — reload'), { status: 409 }))
    await click(document.querySelector('[data-testid="decision-save"]'))
    await flush()
    expect(api.updateDecision.mock.calls[0][2].version).toBe(3)
    expect(api.listDecisions.mock.calls.length).toBeGreaterThan(calls)
    expect(q(c, 'decisions-notice').textContent).toContain('changed by someone else — reload')
  })

  it('health panel lists problems with Verify; hidden when none; shows git_error', async () => {
    api.decisionsHealth.mockResolvedValue({ available: true, git_error: null, ok: false, items: [
      { number: 12, display_id: 'D-012', touches_unmatched: ['src/gone.js'], see_missing: false, changed_since_verified: { count: 2, subjects: ['fix x'] }, unverified_days: null },
    ] })
    const c = await mount()
    expect(q(c, 'health-12').textContent).toContain('src/gone.js')
    expect(q(c, 'health-12').textContent).toContain('fix x')
    await click(q(c, 'health-verify-12'))
    expect(api.verifyDecision).toHaveBeenCalledWith('p1', 12)
    await view.unmount()
    api.decisionsHealth.mockResolvedValue({ available: true, git_error: null, ok: true, items: [] })
    expect(q(await mount(), 'decisions-health')).toBeNull()
    await view.unmount()
    api.decisionsHealth.mockResolvedValue({ available: true, git_error: 'not a git repo', ok: false, items: [] })
    expect(q(await mount(), 'health-git-error').textContent).toBe('not a git repo')
  })
})

describe('elsewhere', () => {
  it('renders a decision_note run-log line', () => {
    expect(renderLine({ type: 'decision_note', payload: { ids: [12, 15], path: 'src/a.js' } }).text).toBe('Showed D-012, D-015 before editing src/a.js')
  })
  it('ground rules list the decisions switch', () => {
    const s = GROUND_SWITCHES.find(([k]) => k === 'decisions')
    expect(s[2]).toMatch(/recorded project decisions/)
  })
})
