import { act } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flush, render, setVisibility } from './testRender'

vi.mock('../api', () => ({
  api: {
    listApprovals: vi.fn(async () => []),
    listTasks: vi.fn(async () => []),
    listSchedules: vi.fn(async () => []),
    myCredentials: vi.fn(async () => ({ claude: 'own' })),
    liveRuns: vi.fn(async () => []),
    usageSummary: vi.fn(async () => ({ today: { cost_usd: 0 } })),
  },
}))

import { api } from '../api'
import { POLL_MS, useHomeData } from './useHomeData'

const me = { id: 'u1', name: 'Me' }
function Probe() {
  useHomeData(me)
  return null
}
const tick = (ms) => act(async () => { await vi.advanceTimersByTimeAsync(ms) })

describe('useHomeData polling', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.clearAllMocks()
    setVisibility('visible')
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('loads once on mount, then every 10s', async () => {
    const r = await render(<Probe />)
    await flush()
    expect(api.liveRuns).toHaveBeenCalledTimes(1)
    await tick(POLL_MS - 1)
    expect(api.liveRuns).toHaveBeenCalledTimes(1)
    await tick(1)
    expect(api.liveRuns).toHaveBeenCalledTimes(2)
    await tick(POLL_MS)
    expect(api.liveRuns).toHaveBeenCalledTimes(3)
    await r.unmount()
  })

  it('skips polls while hidden and catches up when visible again', async () => {
    const r = await render(<Probe />)
    await flush()
    expect(api.liveRuns).toHaveBeenCalledTimes(1)
    await act(async () => setVisibility('hidden'))
    await tick(POLL_MS * 3)
    expect(api.liveRuns).toHaveBeenCalledTimes(1)
    await act(async () => setVisibility('visible'))
    await flush()
    expect(api.liveRuns).toHaveBeenCalledTimes(2)
    await r.unmount()
  })

  it('stops polling after unmount', async () => {
    const r = await render(<Probe />)
    await flush()
    await r.unmount()
    const n = api.liveRuns.mock.calls.length
    await tick(POLL_MS * 3)
    await act(async () => setVisibility('visible'))
    expect(api.liveRuns).toHaveBeenCalledTimes(n)
  })
})
