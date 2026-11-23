import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { flush, render } from '../home/testRender'

vi.mock('../api', () => ({
  api: {
    listWorkflows: vi.fn(async () => [{ id: 'w1', name: 'Ship it', stages: [] }]),
    workflowPresets: vi.fn(async () => []),
    listWorkflowRuns: vi.fn(async () => []),
    listProjects: vi.fn(async () => [{ id: 'p1', name: 'Proj' }]),
    listAgents: vi.fn(async () => [{ name: 'Planner' }]),
    workflowTemplates: vi.fn(),
    exportWorkflow: vi.fn(async () => '# Ship it'),
    importWorkflow: vi.fn(),
    workflowRepoFiles: vi.fn(async () => ({ files: [], reason: '' })),
    createWorkflow: vi.fn(),
    updateWorkflow: vi.fn(),
  },
}))

import { api } from '../api'
import WorkflowsView from '../components/WorkflowsView'

const HINT = "Set the project's own test command in the gate."
const tpl = (key, extra = {}) => ({
  key, name: `T-${key}`, description: 'd', hint: null,
  stages: [{ name: 'Plan', items: [{ agent: 'Planner', prompt: 'p {{input}}' }, { agent: 'Builder', prompt: 'b' }] }],
  ...extra,
})
const TEMPLATES = [
  tpl('software', { hint: HINT, stages: [
    { name: 'Plan', items: [{ agent: 'Planner', prompt: 'p' }, { agent: 'Builder', prompt: 'b' }] },
    { name: 'Tests', kind: 'gate', run: ['npm test'], timeout_min: 60, before: [] },
  ] }),
  tpl('planned'), tpl('documents'), tpl('spike'), tpl('ui'),
]

describe('workflow templates, export, import', () => {
  let view
  beforeAll(() => {
    window.matchMedia ||= () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} })
  })
  afterEach(async () => {
    await view?.unmount()
    view = null
    vi.clearAllMocks()
  })
  const click = async (el) => {
    await act(async () => el.dispatchEvent(new MouseEvent('click', { bubbles: true })))
    await flush()
  }
  const btn = (t) => [...document.body.querySelectorAll('button')].find((b) => b.textContent.replace(/\s/g, '') === t.replace(/\s/g, ''))
  const mount = async () => {
    api.workflowTemplates.mockResolvedValue(TEMPLATES)
    view = await render(<WorkflowsView isAdmin />)
    await flush()
  }

  it('lists 5 templates and a choice pre-fills the editor with the exact hint and missing agents', async () => {
    await mount()
    await click(btn('New from template'))
    await flush()
    expect(document.body.querySelectorAll('[data-testid="template-item"]').length).toBe(5)
    await click(document.body.querySelector('[data-testid="template-item"] button'))
    await flush()
    expect(document.body.querySelector('[data-testid="template-hint"]').textContent).toBe(HINT)
    expect(document.body.querySelector('[data-testid="missing-agents"]').textContent)
      .toContain('Add these helpers from Agents → presets first: Builder')
    const names = [...document.body.querySelectorAll('input')].map((i) => i.value)
    expect(names).toContain('T-software')
    expect(names).toContain('Tests')
  })

  it('shows the server reason when importing pasted text fails with 422', async () => {
    await mount()
    api.importWorkflow.mockRejectedValueOnce(new Error('Workflow file has no json block'))
    await click(btn('Import'))
    const ta = document.body.querySelector('textarea[placeholder^="Paste"]')
    await act(async () => {
      const set = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set
      set.call(ta, 'junk')
      ta.dispatchEvent(new Event('input', { bubbles: true }))
    })
    await flush()
    const ok = [...document.body.querySelectorAll('.ant-modal-footer button')].pop()
    await click(ok)
    await flush()
    expect(api.importWorkflow).toHaveBeenCalledWith({ text: 'junk' })
    expect(document.body.textContent).toContain('Workflow file has no json block')
  })

  it('export calls the api and downloads <name>.md, revoking the url', async () => {
    URL.createObjectURL = vi.fn(() => 'blob:x')
    URL.revokeObjectURL = vi.fn()
    const clicked = []
    const orig = HTMLAnchorElement.prototype.click
    HTMLAnchorElement.prototype.click = function () { clicked.push(this.download) }
    await mount()
    await click(btn('Export'))
    await flush()
    HTMLAnchorElement.prototype.click = orig
    expect(api.exportWorkflow).toHaveBeenCalledWith('w1')
    expect(clicked).toEqual(['Ship it.md'])
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:x')
  })
})
