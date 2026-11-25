import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { flush, render } from '../home/testRender'

vi.mock('../api', () => ({ api: { approveWorkflowStage: vi.fn(), rejectWorkflowStage: vi.fn(), approveAhead: vi.fn(), withdrawAhead: vi.fn() } }))
import { api } from '../api'
import ApprovalCard, { approvalLead } from './ApprovalCard'

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

  it('leads with a plain sentence', async () => {
    const c = await mount()
    expect(c.querySelector('[data-testid="approval-lead"]').textContent).toBe('Waiting for someone to approve this step')
    expect(approvalLead('approved')).toBe('Approved')
    expect(approvalLead('rejected')).toBe('Rejected')
  })
})

describe('ApprovalCard: approve ahead', () => {
  let view
  beforeAll(() => {
    window.matchMedia ||= () => ({ matches: true, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} })
  })
  beforeEach(() => vi.clearAllMocks())
  afterEach(async () => {
    await view?.unmount()
    view = null
  })
  const mount = async (props = {}) => {
    view = await render(<ApprovalCard runId="r1" index={2} stage={{ name: 'Approve plan', ahead: true }} state={{}} future canDecide {...props} />)
    await flush()
    return view.container
  }
  const q = (c, id) => c.querySelector(`[data-testid="${id}"]`)
  const press = async (el) => {
    await act(async () => el.dispatchEvent(new MouseEvent('click', { bubbles: true })))
    await flush()
  }

  it('offers Approve ahead on a step not reached yet, after a plain question', async () => {
    api.approveAhead.mockResolvedValue({})
    const c = await mount()
    await press(q(c, 'approval-ahead-btn'))
    const pop = document.querySelector('.ant-popconfirm')
    expect(pop.textContent).toContain('Approve Approve plan now? The run will go straight past it.')
    expect(api.approveAhead).not.toHaveBeenCalled()
    await press([...pop.querySelectorAll('button')].find((b) => b.textContent.includes('Approve ahead')))
    expect(api.approveAhead).toHaveBeenCalledWith('r1', 2)
  })

  it('is hidden for people who cannot manage the run, and says nothing for them', async () => {
    const c = await mount({ canDecide: false })
    expect(q(c, 'approval-ahead-btn')).toBeNull()
  })

  it.each([{ name: 'Approve release' }, { name: 'Approve release', ahead: false }])('is hidden on a step not marked as allowing it, with a grey note instead', async (stage) => {
    const c = await mount({ stage })
    expect(q(c, 'approval-ahead-btn')).toBeNull()
    expect(q(c, 'approval-needs-you').textContent).toBe('Needs you when the run gets here')
  })

  it('is not offered once the step was reached', async () => {
    const c = await mount({ future: false, state: { status: 'waiting_approval' } })
    expect(q(c, 'approval-ahead-btn')).toBeNull()
    expect(q(c, 'approval-approve')).not.toBeNull()
  })

  it('shows who approved ahead with a Withdraw link', async () => {
    api.withdrawAhead.mockResolvedValue({})
    const c = await mount({ state: { ahead: { by_name: 'Asha', at: '2026-11-25T10:00:00Z' } } })
    expect(q(c, 'approval-ahead').textContent).toContain('Approved ahead by Asha')
    expect(q(c, 'approval-ahead-btn')).toBeNull()
    await press(q(c, 'approval-withdraw'))
    expect(api.withdrawAhead).toHaveBeenCalledWith('r1', 2)
  })

  it('hides Withdraw for people who cannot manage', async () => {
    const c = await mount({ canDecide: false, state: { ahead: { by_name: 'Asha' } } })
    expect(q(c, 'approval-ahead').textContent).toContain('Approved ahead by Asha')
    expect(q(c, 'approval-withdraw')).toBeNull()
  })

  it('after the step is reached the approved display says approved ahead', async () => {
    const c = await mount({ future: false, state: { status: 'approved', decided_by: 'Asha', ahead: { by_name: 'Asha' } } })
    expect(q(c, 'approval-by').textContent).toBe('Approved ahead by Asha')
    expect(q(c, 'approval-ahead')).toBeNull()
  })

  it('maps errors to plain words', async () => {
    api.approveAhead.mockRejectedValue(Object.assign(new Error('this step has already been reached'), { status: 409 }))
    const err = vi.spyOn((await import('antd')).message, 'error').mockImplementation(() => {})
    const c = await mount()
    await press(q(c, 'approval-ahead-btn'))
    await press([...document.querySelector('.ant-popconfirm').querySelectorAll('button')].find((b) => b.textContent.includes('Approve ahead')))
    expect(err).toHaveBeenCalledWith('this step has already been reached')
    api.approveAhead.mockRejectedValue(Object.assign(new Error('Traceback boom'), { status: 500 }))
    await press(q(c, 'approval-ahead-btn'))
    const pops = document.querySelectorAll('.ant-popconfirm')
    await press([...pops[pops.length - 1].querySelectorAll('button')].find((b) => b.textContent.includes('Approve ahead')))
    expect(err).toHaveBeenLastCalledWith("That didn't work. Try again.")
    err.mockRestore()
  })

  it('keeps the note and Withdraw while the step is held', async () => {
    const c = await mount({ future: false, state: { status: 'held', ahead: { by_name: 'Asha' } } })
    expect(q(c, 'approval-ahead').textContent).toContain('Approved ahead by Asha')
    expect(q(c, 'approval-withdraw')).not.toBeNull()
  })
})
