import { act } from 'react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { flush, render } from './testRender'

vi.mock('../api', () => ({
  api: {
    listApprovals: vi.fn(),
    listActionApprovals: vi.fn(),
    approveAction: vi.fn(),
    listTasks: vi.fn(),
    listSchedules: vi.fn(),
    myCredentials: vi.fn(),
    liveRuns: vi.fn(),
    usageSummary: vi.fn(),
    approveTask: vi.fn(),
  },
}))

import { api } from '../api'
import HomeView from './HomeView'

const me = { id: 'u1', name: 'Me', email: 'me@x.io' }
const boom = () => Promise.reject(new Error('boom'))

function okAll() {
  api.listApprovals.mockImplementation(async () => [])
  api.listActionApprovals.mockImplementation(async () => [])
  api.listTasks.mockImplementation(async () => [])
  api.listSchedules.mockImplementation(async () => [])
  api.myCredentials.mockImplementation(async () => ({ claude: 'own' }))
  api.liveRuns.mockImplementation(async () => [])
  api.usageSummary.mockImplementation(async () => ({ today: { cost_usd: 0 } }))
}

const panel = (c, id) => c.querySelector(`[data-testid="${id}"]`)
const text = (c, id) => panel(c, id).textContent

let view
async function mount() {
  view = await render(<HomeView user={me} navigate={() => {}} />)
  await flush()
  await flush()
  return view.container
}

beforeAll(() => {
  window.matchMedia ||= () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} })
})
beforeEach(() => {
  vi.clearAllMocks()
  okAll()
})
afterEach(async () => {
  await view?.unmount()
  view = null
})

describe('HomeView empty vs error states', () => {
  it('shows every empty state when all sources load and are empty', async () => {
    const c = await mount()
    expect(text(c, 'home-needs-you')).toContain("You're all caught up")
    expect(text(c, 'home-recent')).toContain('Nothing finished yet')
    expect(text(c, 'home-live')).toContain('No workflows running right now')
    expect(c.querySelector('.co-home-error')).toBeNull()
    expect(text(c, 'home-summary')).toContain('Nothing needs you')
  })

  it('live: an error with no data shows only the retry alert', async () => {
    api.liveRuns.mockImplementation(boom)
    const c = await mount()
    expect(text(c, 'home-live')).toContain("Couldn't load live workflows")
    expect(panel(c, 'home-live').querySelector('.co-home-error button')).not.toBeNull()
    expect(text(c, 'home-live')).not.toContain('No workflows running right now')
  })

  it('recent: an error with no data shows only the retry alert', async () => {
    api.listTasks.mockImplementation(async (p) => (p?.status === 'failed' ? [] : boom()))
    const c = await mount()
    expect(text(c, 'home-recent')).toContain("Couldn't load your recent tasks")
    expect(text(c, 'home-recent')).not.toContain('Nothing finished yet')
  })

  it.each(['listApprovals', 'listActionApprovals', 'myCredentials', 'listSchedules'])(
    'needs you: a failed %s never claims "all caught up"',
    async (fn) => {
      api[fn].mockImplementation(boom)
      const c = await mount()
      expect(text(c, 'home-needs-you')).toContain("Couldn't load everything that needs you")
      expect(text(c, 'home-needs-you')).not.toContain("You're all caught up")
      expect(text(c, 'home-summary')).not.toContain('Nothing needs you')
    },
  )

  it('needs you: failed-tasks fetch error also blocks the all-clear', async () => {
    api.listTasks.mockImplementation(async (p) => (p?.status === 'failed' ? boom() : []))
    const c = await mount()
    expect(text(c, 'home-needs-you')).not.toContain("You're all caught up")
  })

  it('keeps showing items it has alongside the error', async () => {
    api.listApprovals.mockImplementation(async () => [{ id: 'a1', title: 'Deploy it', created_at: '2026-11-20T10:00:00' }])
    api.myCredentials.mockImplementation(boom)
    const c = await mount()
    expect(text(c, 'home-needs-you')).toContain('Deploy it')
    expect(text(c, 'home-needs-you')).toContain("Couldn't load everything that needs you")
  })
})

describe('HomeView: action approvals', () => {
  const full = '<b>git reset --hard</b>\nsecond line ' + 'z'.repeat(300)
  const action = { id: 'r1', task_id: 't1', task_title: 'Clean', tool: 'Bash', summary: full,
    rule_ids: ['git-reset-hard'], created_at: '2026-11-20T10:00:00', can_decide: true }

  it('never approves from Home (Ruling R27): Review opens the full card; plain text; full command in the tooltip', async () => {
    api.listActionApprovals.mockImplementation(async () => [action])
    const navigate = vi.fn()
    view = await render(<HomeView user={me} navigate={navigate} />)
    await flush()
    await flush()
    const row = view.container.querySelector('[data-testid="needs-action"]')
    expect(row.textContent).toContain('<b>git reset --hard</b>')
    expect(row.querySelector('b')).toBeNull()
    expect(row.querySelector('[title]').getAttribute('title')).toBe(`Bash: ${full}`)
    const labels = [...row.querySelectorAll('button')].map((b) => b.textContent.trim())
    expect(labels).toEqual(['Review'])
    await act(async () => row.querySelector('button').dispatchEvent(new MouseEvent('click', { bubbles: true })))
    expect(navigate).toHaveBeenCalledWith({ page: 'approvals', id: 'r1' })
    expect(api.approveAction).not.toHaveBeenCalled()
  })

  it('task approvals on Home keep Approve', async () => {
    api.listApprovals.mockImplementation(async () => [{ id: 'a1', title: 'Deploy', created_at: '2026-11-20T10:00:00' }])
    view = await render(<HomeView user={me} navigate={() => {}} />)
    await flush()
    await flush()
    const row = view.container.querySelector('[data-testid="needs-approval"]')
    expect([...row.querySelectorAll('button')].map((b) => b.textContent.trim())).toEqual(['Approve', 'Open'])
  })
})
