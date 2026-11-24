import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { flush, render } from '../home/testRender'

vi.mock('../api', () => ({
  api: {
    listWorkflows: vi.fn(),
    workflowPresets: vi.fn(async () => []),
    listWorkflowRuns: vi.fn(async () => []),
    listProjects: vi.fn(async () => [{ id: 'p1', name: 'Proj', directory: '/x' }]),
    updateWorkflow: vi.fn(async () => ({})),
  },
}))

import { api } from '../api'
import WorkflowsView from './WorkflowsView'

describe('WorkflowsView: ⌘K run request', () => {
  let view
  beforeAll(() => {
    window.matchMedia ||= () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} })
  })
  afterEach(async () => {
    await view?.unmount()
    view = null
    vi.clearAllMocks()
  })

  it('clears the request when the initial load fails, so it does not fire on a later visit', async () => {
    api.listWorkflows.mockRejectedValueOnce(new Error('boom'))
    const onRunHandled = vi.fn()
    view = await render(<WorkflowsView runWorkflowId="w1" onRunHandled={onRunHandled} />)
    await flush()
    expect(onRunHandled).toHaveBeenCalledTimes(1)
  })

  it('keeps the request while loading and handles it once the list is in', async () => {
    api.listWorkflows.mockResolvedValueOnce([{ id: 'w1', name: 'Ship', stages: [] }])
    const onRunHandled = vi.fn()
    view = await render(<WorkflowsView runWorkflowId="w1" onRunHandled={onRunHandled} />)
    await flush()
    expect(onRunHandled).toHaveBeenCalledTimes(1)
  })

  it('does nothing without a request when the load fails', async () => {
    api.listWorkflows.mockRejectedValueOnce(new Error('boom'))
    const onRunHandled = vi.fn()
    view = await render(<WorkflowsView onRunHandled={onRunHandled} />)
    await flush()
    expect(onRunHandled).not.toHaveBeenCalled()
  })
})

describe('WorkflowsView: stage kinds', () => {
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
  const byText = (t) => [...document.body.querySelectorAll('button')].find((b) => b.textContent.replace(/\s/g, '') === t)
  const menuItem = (t) => [...document.body.querySelectorAll('.ant-dropdown-menu-item')].find((li) => li.textContent.trim() === t)

  async function saveEdit(stages, extra = {}, agents = []) {
    api.listWorkflows.mockResolvedValue([{ id: 'w1', name: 'Ship', stages, ...extra }])
    view = await render(<WorkflowsView isAdmin agents={agents} />)
    await flush()
    await click(document.body.querySelector('[data-testid="workflow-more"]'))
    await click(menuItem('Edit'))
    await flush()
    await click(byText('Save'))
    await new Promise((r) => setTimeout(r, 50))
    await flush()
    return api.updateWorkflow.mock.calls[0]?.[1]
  }

  it('each card draws its steps as a small flow', async () => {
    api.listWorkflows.mockResolvedValue([
      { id: 'w1', name: 'Ship', stages: [{ name: 'Dev', items: [{ agent: 'a' }, { agent: 'b' }] }, { name: 'Tests', kind: 'gate', run: ['x'] }, { name: 'Sign', kind: 'approval' }] },
    ])
    view = await render(<WorkflowsView />)
    await flush()
    const card = view.container.querySelector('[data-testid="workflow-card"]')
    const canvas = card.querySelector('[data-testid="flow-canvas"]')
    expect(canvas.className).toContain('co-flow-mini')
    expect([...canvas.querySelectorAll('[data-testid="flow-step"]')].map((p) => p.dataset.kind)).toEqual(['agent', 'gate', 'approval'])
    // two agents side by side in the first step, then the check and the person
    expect([...canvas.querySelectorAll('[data-testid="flow-box"]')].map((b) => b.textContent)).toEqual(['a', 'b', 'Tests', 'Sign'])
    expect(canvas.querySelector('[data-testid="flow-box"]').getAttribute('title')).toBe('Step 1, Agent 1 of 2: a')
    expect(view.container.textContent).not.toMatch(/gate|pipeline/i)
  })

  it('saves a gate stage with kind, run, project, timeout and no items', async () => {
    const p = await saveEdit([{ name: 'Tests', kind: 'gate', project: 'p1', run: ['pytest -q'], timeout_min: 30 }])
    expect(p.stages).toEqual([{ name: 'Tests', kind: 'gate', project: 'p1', run: ['pytest -q'], before: [], timeout_min: 30 }])
  })

  it('saves an approval stage', async () => {
    const p = await saveEdit([{ name: 'Sign', kind: 'approval', message: 'ok?' }])
    expect(p.stages).toEqual([{ name: 'Sign', kind: 'approval', message: 'ok?', before: [] }])
  })

  it('saves an old agent stage without a kind key', async () => {
    const p = await saveEdit([{ name: 'Dev', items: [{ agent: 'a', prompt: 'do' }] }])
    expect(p.stages).toEqual([{ name: 'Dev', items: [{ agent: 'a', prompt: 'do' }] }])
  })

  it('an old workflow saves exactly as before: no budget, outputs, auto_retry or fix keys', async () => {
    const stages = [
      { name: 'Dev', items: [{ agent: 'a', prompt: 'do' }] },
      { name: 'Tests', kind: 'gate', before: [], project: 'p1', run: ['pytest -q'], timeout_min: 60 },
    ]
    const p = await saveEdit(stages, { description: 'd', budget_usd: null })
    expect(JSON.stringify(p)).toBe(JSON.stringify({ name: 'Ship', description: 'd', stages }))
  })

  it('keeps hand-over names, the retry switch, the fix agent and the spending limit', async () => {
    const stages = [
      { name: 'Plan', auto_retry: true, items: [{ agent: 'a', prompt: 'plan', outputs: ['plan_path'] }] },
      { name: 'Tests', kind: 'gate', before: [], project: 'p1', run: ['pytest -q'], timeout_min: 60, fix: { agent: 'ag1', rounds: 2 } },
    ]
    const agents = [{ id: 'ag1', name: 'Fixer', project_id: 'p1' }, { id: 'ag2', name: 'Other', project_id: 'p2' }]
    const p = await saveEdit(stages, { budget_usd: 2.5 }, agents)
    expect(p.budget_usd).toBe(2.5)
    expect(p.stages).toEqual(stages)
    // the editor shows the plain labels, never the technical words
    const drawer = document.body.querySelector('.ant-drawer')
    const text = drawer.textContent
    expect(text).toContain('Spending limit for each run (optional)')
    expect(text).toContain('What should this agent hand over? (optional)')
    expect(text).toContain('Try once more if it fails')
    expect(text).toContain('If the check fails, ask an agent to fix it (optional)')
    expect(text).not.toMatch(/\b(gate|kind|stage|pre-flight|pipeline)\b/i)
  })

  it('clearing a saved spending limit sends null', async () => {
    api.listWorkflows.mockResolvedValue([{ id: 'w1', name: 'Ship', budget_usd: 4, stages: [{ name: 'Dev', items: [{ agent: 'a', prompt: 'do' }] }] }])
    view = await render(<WorkflowsView isAdmin />)
    await flush()
    await click(document.body.querySelector('[data-testid="workflow-more"]'))
    await click(menuItem('Edit'))
    await flush()
    // the limit stays tucked away behind Settings, with a chip that opens it
    const panel = document.body.querySelector('[data-testid="workflow-settings-panel"]')
    expect(panel.hidden).toBe(true)
    const chip = document.body.querySelector('[data-testid="workflow-limit-chip"]')
    expect(chip.textContent).toBe('Limit $4.00')
    await click(chip)
    expect(panel.hidden).toBe(false)
    const input = document.body.querySelector('[data-testid="workflow-budget"] input') || document.body.querySelector('input[aria-label="Spending limit for each run"]')
    expect(input.value).toBe('4.00')
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, '')
      input.dispatchEvent(new Event('input', { bubbles: true }))
      input.dispatchEvent(new Event('change', { bubbles: true }))
    })
    await flush()
    await click(byText('Save'))
    await new Promise((r) => setTimeout(r, 50))
    await flush()
    expect(api.updateWorkflow.mock.calls[0][1].budget_usd).toBeNull()
  })

  it('shows the server reason under the form', async () => {
    api.updateWorkflow.mockRejectedValueOnce(new Error("Step 'Tests': a command cannot contain &"))
    await saveEdit([{ name: 'Tests', kind: 'gate', project: 'p1', run: ['pytest -q'] }])
    expect(document.body.textContent).toContain("Step 'Tests': a command cannot contain &")
  })
})

describe('WorkflowsView: list page', () => {
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

  it('New workflow opens a menu with template, blank and import', async () => {
    api.listWorkflows.mockResolvedValue([])
    view = await render(<WorkflowsView isAdmin />)
    await flush()
    await click(document.body.querySelector('[data-testid="new-workflow"]'))
    const items = [...document.body.querySelectorAll('.ant-dropdown-menu-item')].map((li) => li.textContent.trim())
    expect(items).toEqual(['Start from a template', 'Start blank', 'Import from a file'])
  })

  it('shows a friendly empty state with a template button', async () => {
    api.listWorkflows.mockResolvedValue([])
    view = await render(<WorkflowsView isAdmin />)
    await flush()
    const empty = view.container.querySelector('[data-testid="workflows-empty"]')
    expect(empty.textContent).toContain('No workflows yet')
    expect(empty.querySelector('button').textContent).toContain('Start from a template')
  })

  it('recent runs show a plain status and "step N of M"', async () => {
    api.listWorkflows.mockResolvedValue([])
    api.listWorkflowRuns.mockResolvedValue([
      { id: 'r1', name: 'Ship', input: 'PR 1', status: 'paused', current_stage: 1, stages: [{}, {}, {}, {}, {}] },
      { id: 'r2', name: 'Ship', input: 'PR 2', status: 'pending', current_stage: 0, stages: [{}] },
    ])
    view = await render(<WorkflowsView />)
    await flush()
    const links = [...view.container.querySelectorAll('[data-testid="workflow-run-link"]')]
    expect(links[0].querySelector('[data-testid="run-status"]').textContent).toBe('Waiting for you')
    expect(links[0].textContent).toContain('step 2 of 5')
    expect(links[1].querySelector('[data-testid="run-status"]').textContent).toBe('Starting')
  })
})

describe('WorkflowsView: the canvas editor', () => {
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
  const byText = (t) => [...document.body.querySelectorAll('button')].find((b) => b.textContent.replace(/\s/g, '') === t)
  const menuItem = (t) => [...document.body.querySelectorAll('.ant-dropdown-menu-item')].find((li) => li.textContent.trim() === t)
  const drawer = () => document.body.querySelector('.ant-drawer')
  const box = (label) => [...drawer().querySelectorAll('[data-testid="flow-box"]')].find((b) => b.getAttribute('aria-label').startsWith(label))
  // Shown: neither it nor an ancestor is hidden.
  const shown = (el) => {
    for (let n = el; n && n !== document.body; n = n.parentElement) if (n.hidden) return false
    return true
  }
  const cards = () => [...drawer().querySelectorAll('[data-testid="step-card"]')]

  async function openEdit(stages, agents = []) {
    api.listWorkflows.mockResolvedValue([{ id: 'w1', name: 'Ship', stages }])
    view = await render(<WorkflowsView isAdmin agents={agents} />)
    await flush()
    await click(document.body.querySelector('[data-testid="workflow-more"]'))
    await click(menuItem('Edit'))
    await flush()
  }
  const save = async () => {
    await click(byText('Save'))
    await new Promise((r) => setTimeout(r, 50))
    await flush()
  }

  const STAGES = [
    { name: 'Build', items: [{ agent: 'a', prompt: 'plan' }, { agent: 'b', prompt: 'build' }] },
    { name: 'Tests', kind: 'gate', before: [], project: 'p1', run: ['pytest -q'], timeout_min: 60, fix: { agent: 'ag1', rounds: 2 } },
    { name: 'Sign', kind: 'approval', message: 'ok?', before: [] },
  ]
  const AGENTS = [{ id: 'ag1', name: 'Fixer', project_id: 'p1' }]

  it('draws the whole flow and shows only the selected box\'s fields', async () => {
    await openEdit(STAGES, AGENTS)
    expect(drawer().querySelectorAll('[data-testid="flow-box"]').length).toBe(5)
    expect(shown(drawer().querySelector('[data-testid="step-panel-hint"]'))).toBe(true)
    expect(drawer().querySelector('[data-testid="step-panel-hint"]').textContent).toContain('Click a box to edit it, or + to add a step.')
    expect(cards().some(shown)).toBe(false)

    await click(box('Step 2, Check: Tests'))
    expect(cards().map(shown)).toEqual([false, true, false])
    expect(drawer().querySelector('[data-testid="step-panel"]').textContent).toContain('Step 2 · Check')
    expect(shown(cards()[1].querySelector('[aria-label="Command"]'))).toBe(true)
    expect(box('Step 2, Check: Tests').getAttribute('aria-pressed')).toBe('true')

    await click(box('Step 1, Agent 2 of 2: b'))
    expect(cards().map(shown)).toEqual([true, false, false])
    const items = [...cards()[0].querySelectorAll('[data-testid="agent-item"]')]
    expect(items.map(shown)).toEqual([false, true])
    expect(cards()[0].textContent).toContain('Try once more if it fails')
    expect(cards()[0].textContent).not.toContain('Add another agent')

    await click(box('Step 2, Fixer: Fixer'))
    expect(cards().map(shown)).toEqual([false, true, false])
    expect(shown(cards()[1].querySelector('[data-testid="gate-fix"]'))).toBe(true)
    expect(shown(cards()[1].querySelector('[aria-label="Command"]'))).toBe(false)

    await click(box('Step 3, Ask a person: Sign'))
    expect(shown(cards()[2].querySelector('textarea'))).toBe(true)
    expect(drawer().textContent).not.toMatch(/\b(gate|kind|stage|pre-flight|pipeline)\b/i)
  })

  it('a validation error selects the box with the problem and marks it', async () => {
    await openEdit([
      { name: 'Dev', items: [{ agent: 'a', prompt: 'do' }] },
      { name: 'Review', items: [{ agent: 'a', prompt: 'x' }, { agent: 'b' }] },
    ])
    await save()
    expect(api.updateWorkflow).not.toHaveBeenCalled()
    const bad = box('Step 2, Agent 2 of 2: b')
    expect(bad.getAttribute('aria-pressed')).toBe('true')
    expect(bad.className).toContain('co-flow-err')
    expect(bad.getAttribute('aria-label')).toContain('needs attention')
    expect(cards().map(shown)).toEqual([false, true])
    const items = [...cards()[1].querySelectorAll('[data-testid="agent-item"]')]
    expect(items.map(shown)).toEqual([false, true])
    expect(items[1].textContent).toContain('Tell the agent what to do')
  })

  it('moving and deleting steps on the canvas changes the saved order', async () => {
    await openEdit([
      { name: 'A', items: [{ agent: 'a', prompt: '1' }] },
      { name: 'B', items: [{ agent: 'b', prompt: '2' }] },
      { name: 'C', kind: 'approval', message: 'ok?', before: [] },
    ])
    await click(drawer().querySelectorAll('[data-testid="flow-move-right"]')[0])
    await click(drawer().querySelectorAll('[data-testid="flow-remove"]')[2])
    await click([...document.body.querySelectorAll('.ant-popconfirm .ant-btn')].find((b) => b.textContent === 'Delete'))
    await save()
    expect(api.updateWorkflow.mock.calls[0][1].stages).toEqual([
      { name: 'B', items: [{ agent: 'b', prompt: '2' }] },
      { name: 'A', items: [{ agent: 'a', prompt: '1' }] },
    ])
  })

  it('a blank workflow starts as Start → + → Done; + adds a step and selects it', async () => {
    api.listWorkflows.mockResolvedValue([])
    view = await render(<WorkflowsView isAdmin />)
    await flush()
    await click(document.body.querySelector('[data-testid="new-workflow"]'))
    await click(menuItem('Start blank'))
    await flush()
    const plus = drawer().querySelectorAll('[data-testid="flow-plus"]')
    expect(plus.length).toBe(1)
    expect(plus[0].className).toContain('co-flow-plus-pulse')
    await click(plus[0])
    await click(document.body.querySelector('[data-testid="flow-add-gate"]'))
    expect(cards().length).toBe(1)
    expect(shown(cards()[0])).toBe(true)
    expect(box('Step 1, Check: Check').getAttribute('aria-pressed')).toBe('true')
    // "+ agent" adds a parallel agent to an agents step
    await click(drawer().querySelectorAll('[data-testid="flow-plus"]')[1])
    await click([...document.body.querySelectorAll('[data-testid="flow-add-agent"]')].pop())
    await click(drawer().querySelector('[data-testid="flow-add-agent"]'))
    expect(drawer().querySelectorAll('[data-testid="flow-box"]').length).toBe(3)
    expect(box('Step 2, Agent 2 of 2').getAttribute('aria-pressed')).toBe('true')
  })
})

describe('WorkflowsView: opening a card', () => {
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
  const W = { id: 'w1', name: 'Ship', description: 'ships it', stages: [{ name: 'Dev', items: [{ agent: 'a', prompt: 'do' }] }, { name: 'Tests', kind: 'gate', run: ['npm test'] }] }

  it('the card body opens the full read-only canvas with Edit and Run', async () => {
    api.listWorkflows.mockResolvedValue([W])
    view = await render(<WorkflowsView isAdmin />)
    await flush()
    await click(view.container.querySelector('[data-testid="flow-open"]'))
    const pv = document.body.querySelector('[data-testid="flow-preview"]')
    expect(pv).not.toBeNull()
    const canvas = pv.querySelector('[data-testid="flow-canvas"]')
    expect(canvas.className).toContain('co-flow-full')
    expect(canvas.querySelector('[data-testid="flow-plus"]')).toBeNull()
    expect(canvas.textContent).toContain('npm test')
    expect(document.body.querySelector('[data-testid="preview-edit"]')).not.toBeNull()
    await click(document.body.querySelector('[data-testid="preview-run"]'))
    expect(document.body.querySelector('textarea[aria-label="What to run it on"]')).not.toBeNull()
  })

  it('people who are not admins get Run but no Edit; Enter on the drawing opens it', async () => {
    api.listWorkflows.mockResolvedValue([W])
    view = await render(<WorkflowsView />)
    await flush()
    const open = view.container.querySelector('[data-testid="flow-open"]')
    await act(async () => open.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })))
    await flush()
    expect(document.body.querySelector('[data-testid="flow-preview"]')).not.toBeNull()
    expect(document.body.querySelector('[data-testid="preview-edit"]')).toBeNull()
    expect(document.body.querySelector('[data-testid="preview-run"]')).not.toBeNull()
  })

  it('Edit in the view opens the editor', async () => {
    api.listWorkflows.mockResolvedValue([W])
    view = await render(<WorkflowsView isAdmin />)
    await flush()
    await click(view.container.querySelector('.co-wf-card-desc'))
    await click(document.body.querySelector('[data-testid="preview-edit"]'))
    await flush()
    expect(document.body.querySelector('.ant-drawer-open .ant-drawer-title').textContent).toBe('Edit “Ship”')
  })
})
