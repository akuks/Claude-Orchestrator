import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { flush, render } from '../home/testRender'

vi.mock('../api', () => ({ api: { approveWorkflowStage: vi.fn(), rejectWorkflowStage: vi.fn() } }))
import { api } from '../api'
import ApprovalCard from './ApprovalCard'

describe('ApprovalCard', () => {
  let view
  beforeAll(() => {
    window.matchMedia ||= () => ({ matches: true, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} })
  })
  beforeEach(() => vi.clearAllMocks())
  afterEach(async () => {
    await view?.unmount()
    view = null
  })
  const mount = async () => {
    view = await render(<ApprovalCard runId="r1" index={2} stage={{ message: 'Ship it?' }} state={{ status: 'waiting_approval' }} />)
    return view.container
  }

  it('shows the message and Approve calls the api', async () => {
    api.approveWorkflowStage.mockResolvedValue({})
    const c = await mount()
    expect(c.querySelector('[data-testid="approval-message"]').textContent).toBe('Ship it?')
    await act(async () => c.querySelector('[data-testid="approval-approve"]').click())
    await flush()
    expect(api.approveWorkflowStage).toHaveBeenCalledWith('r1', 2)
  })
  it('Reject is blocked without a reason', async () => {
    api.rejectWorkflowStage.mockResolvedValue({})
    const c = await mount()
    await act(async () => c.querySelector('[data-testid="approval-reject"]').click())
    const btn = c.querySelector('[data-testid="approval-confirm-reject"]')
    expect(btn.disabled).toBe(true)
    await act(async () => btn.click())
    expect(api.rejectWorkflowStage).not.toHaveBeenCalled()
  })
})
