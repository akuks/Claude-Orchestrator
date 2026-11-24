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
import { GATE_HINT, templateHint } from './TemplatePicker'

// The server's wording; the editor shows GATE_HINT in its place.
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
  const menuItem = (t) => [...document.body.querySelectorAll('.ant-dropdown-menu-item')].find((li) => li.textContent.trim() === t)
  const openMenu = async (t) => {
    await click(document.body.querySelector('[data-testid="new-workflow"]'))
    await click(menuItem(t))
  }
  const mount = async () => {
    api.workflowTemplates.mockResolvedValue(TEMPLATES)
    view = await render(<WorkflowsView isAdmin />)
    await flush()
  }

  it('lists 5 templates and a choice pre-fills the editor with the exact hint and missing agents', async () => {
    await mount()
    await openMenu('Start from a template')
    await flush()
    expect(document.body.querySelectorAll('[data-testid="template-item"]').length).toBe(5)
    await click(document.body.querySelector('[data-testid="template-item"] button'))
    await flush()
    expect(document.body.querySelector('[data-testid="template-hint"]').textContent).toBe(GATE_HINT)
    expect(GATE_HINT).toBe("Set your project's own test command in the check step.")
    expect(document.body.querySelector('[data-testid="missing-agents"]').textContent)
      .toContain('Add these helpers from Agents → presets first: Builder')
    const names = [...document.body.querySelectorAll('input')].map((i) => i.value)
    expect(names).toContain('T-software')
    expect(names).toContain('Tests')
  })

  it('shows the server reason when importing pasted text fails with 422', async () => {
    await mount()
    api.importWorkflow.mockRejectedValueOnce(new Error('Workflow file has no json block'))
    await openMenu('Import from a file')
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
    await click(document.body.querySelector('[data-testid="workflow-more"]'))
    await click(menuItem('Export'))
    await flush()
    HTMLAnchorElement.prototype.click = orig
    expect(api.exportWorkflow).toHaveBeenCalledWith('w1')
    expect(clicked).toEqual(['Ship it.md'])
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:x')
  })

  it('shows the team\'s templates as a second group and uses them like the built-in ones', async () => {
    api.workflowPresets.mockResolvedValueOnce([{ name: 'Team flow', description: 'ours', stages: [{ name: 'Dev', items: [{ agent: 'Planner', prompt: 'x' }] }] }])
    await mount()
    await openMenu('Start from a template')
    await flush()
    const team = document.body.querySelector('[data-testid="team-templates"]')
    expect(team.previousElementSibling.textContent).toBe("Your team's")
    expect(team.textContent).toContain('Team flow')
    await click(team.querySelector('button'))
    await flush()
    const names = [...document.body.querySelectorAll('input')].map((i) => i.value)
    expect(names).toContain('Team flow')
    expect(api.createWorkflow).not.toHaveBeenCalled()
  })
})

describe('template cards draw their steps', () => {
  let view
  afterEach(async () => {
    await view?.unmount()
    view = null
    vi.clearAllMocks()
  })
  const click = async (el) => {
    await act(async () => el.dispatchEvent(new MouseEvent('click', { bubbles: true })))
    await flush()
  }

  it('each template shows a small flow; opening it shows the full canvas with Use this', async () => {
    api.workflowTemplates.mockResolvedValue(TEMPLATES)
    view = await render(<WorkflowsView isAdmin />)
    await flush()
    await click(document.body.querySelector('[data-testid="new-workflow"]'))
    await click([...document.body.querySelectorAll('.ant-dropdown-menu-item')].find((li) => li.textContent.trim() === 'Start from a template'))
    await flush()
    const first = document.body.querySelector('[data-testid="template-item"]')
    const mini = first.querySelector('[data-testid="flow-canvas"]')
    expect(mini.className).toContain('co-flow-mini')
    expect([...mini.querySelectorAll('[data-testid="flow-box"]')].map((b) => b.textContent)).toEqual(['Planner', 'Builder', 'Tests'])
    await click(first.querySelector('[data-testid="flow-open"]'))
    const pv = document.body.querySelector('[data-testid="flow-preview"]')
    expect(pv.querySelectorAll('[data-testid="flow-box"]').length).toBe(3)
    await click(document.body.querySelector('[data-testid="preview-use"]'))
    await flush()
    const names = [...document.body.querySelectorAll('input')].map((i) => i.value)
    expect(names).toContain('T-software')
  })
})

describe('templateHint', () => {
  it('shows the plain hint for the server wording and when a check step has none', () => {
    expect(templateHint({ hint: HINT, stages: [] })).toBe(GATE_HINT)
    expect(templateHint({ hint: null, stages: [{ kind: 'gate' }] })).toBe(GATE_HINT)
    expect(templateHint({ hint: null, stages: [{ name: 'a' }] })).toBeNull()
    expect(templateHint({ hint: 'Other', stages: [] })).toBe('Other')
  })
})
