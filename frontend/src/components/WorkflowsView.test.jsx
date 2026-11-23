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

  async function saveEdit(stages) {
    api.listWorkflows.mockResolvedValue([{ id: 'w1', name: 'Ship', stages }])
    view = await render(<WorkflowsView isAdmin />)
    await flush()
    await click(byText('Edit'))
    await flush()
    await click(byText('Save'))
    await new Promise((r) => setTimeout(r, 50))
    await flush()
    return api.updateWorkflow.mock.calls[0]?.[1]
  }

  it('lists gate and approval stages with markers', async () => {
    api.listWorkflows.mockResolvedValue([
      { id: 'w1', name: 'Ship', stages: [{ name: 'Tests', kind: 'gate', run: ['x'] }, { name: 'Sign', kind: 'approval' }] },
    ])
    view = await render(<WorkflowsView />)
    await flush()
    expect(view.container.textContent).toContain('⚙ Tests')
    expect(view.container.textContent).toContain('✋ Sign')
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

  it('shows the server reason under the form', async () => {
    api.updateWorkflow.mockRejectedValueOnce(new Error("Step 'Tests': a command cannot contain &"))
    await saveEdit([{ name: 'Tests', kind: 'gate', project: 'p1', run: ['pytest -q'] }])
    expect(document.body.textContent).toContain("Step 'Tests': a command cannot contain &")
  })
})
