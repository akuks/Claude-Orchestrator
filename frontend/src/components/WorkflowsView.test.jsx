import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { flush, render } from '../home/testRender'

vi.mock('../api', () => ({
  api: {
    listWorkflows: vi.fn(),
    workflowPresets: vi.fn(async () => []),
    listWorkflowRuns: vi.fn(async () => []),
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
