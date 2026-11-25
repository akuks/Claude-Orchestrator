import { act } from 'react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { flush, render } from '../home/testRender'

vi.mock('../api', () => ({ api: { systemHealth: vi.fn(), retention: vi.fn(), saveRetention: vi.fn(), runRetention: vi.fn(), acceptRootCommit: vi.fn() } }))

import { api } from '../api'
import SystemHealthView from './SystemHealthView'
import { detailRows, overallLine, sortChecks } from './health'

const mk = (id, status, extra = {}) => ({ id, title: `Title ${id}`, status, summary: `Summary ${id}`, ...extra })
const DATA = {
  checked_at: new Date(Date.now() - 5 * 60000).toISOString(),
  overall: 'problem',
  checks: [
    mk('a', 'ok', { detail: { version: '1.2.3' } }),
    mk('b', 'attention'),
    mk('c', 'problem', { detail: { why: 'it hung' }, fix: 'Restart the app to restore it.' }),
    mk('d', 'ok'),
    mk('e', 'problem'),
  ],
}

const RET = { enabled: false, days: 60, choices: [30, 60, 90], tasks: 12, bytes: 12400000, last_run: null, last_count: null, running: false }
let view
beforeAll(() => {
  window.matchMedia ||= () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} })
})
beforeEach(() => {
  vi.clearAllMocks()
  api.systemHealth.mockResolvedValue(DATA)
  api.retention.mockResolvedValue(RET)
})
afterEach(async () => {
  await view?.unmount()
  view = null
})
const mount = async () => {
  view = await render(<SystemHealthView />)
  await flush()
  await flush()
  return view.container
}
const q = (c, id) => c.querySelector(`[data-testid="${id}"]`)

describe('health helpers', () => {
  it('words the overall line exactly', () => {
    expect(overallLine([mk('a', 'ok')])).toBe('Everything looks good')
    expect(overallLine([mk('a', 'attention'), mk('b', 'attention'), mk('c', 'ok')])).toBe('2 things need attention')
    expect(overallLine([mk('a', 'attention')])).toBe('1 thing needs attention')
    expect(overallLine([mk('a', 'problem'), mk('b', 'attention')])).toBe('1 problem')
    expect(overallLine([mk('a', 'problem'), mk('b', 'problem')])).toBe('2 problems')
  })
  it('sorts problems, then attention, then ok, keeping order inside a group', () => {
    expect(sortChecks(DATA.checks).map((c) => c.id)).toEqual(['c', 'e', 'b', 'a', 'd'])
  })
  it('turns details into rows', () => {
    expect(detailRows({ free_gb: 12, servers: [{ name: 'x', status: 'healthy' }] })).toEqual([
      ['free gb', '12'],
      ['servers', 'name: x, status: healthy'],
    ])
  })
})

describe('SystemHealthView', () => {
  it('shows the overall line and when it was checked', async () => {
    const c = await mount()
    expect(q(c, 'health-overall').textContent).toBe('2 problems')
    expect(q(c, 'health-checked').textContent).toBe('Last checked 5 minutes ago')
  })

  it('puts problems first, then attention, then ok', async () => {
    const c = await mount()
    const ids = [...c.querySelectorAll('[data-testid^="health-"][data-status]')].map((n) => n.getAttribute('data-status'))
    expect(ids).toEqual(['problem', 'problem', 'attention', 'ok', 'ok'])
    expect(q(c, 'health-grid').firstElementChild.getAttribute('data-testid')).toBe('health-c')
  })

  it('keeps Details collapsed, with the technical strings and the fix inside', async () => {
    const c = await mount()
    const d = q(c, 'health-c').querySelector('details')
    expect(d.open).toBe(false)
    expect(d.textContent).toContain('Restart the app to restore it.')
    expect(q(c, 'health-c').querySelector('.co-health-summary').textContent).toBe('Summary c')
    expect(q(c, 'health-b').querySelector('details')).toBeNull() // nothing to show
  })

  it('Check again refetches, skipping the server cache', async () => {
    const c = await mount()
    expect(api.systemHealth).toHaveBeenCalledTimes(1)
    expect(api.systemHealth).toHaveBeenLastCalledWith(false)
    await act(async () => {
      q(c, 'health-refresh').dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })
    await flush()
    expect(api.systemHealth).toHaveBeenCalledTimes(2)
    expect(api.systemHealth).toHaveBeenLastCalledWith(true)
  })

  it('says everything is good when it is', async () => {
    api.systemHealth.mockResolvedValue({ checked_at: DATA.checked_at, overall: 'ok', checks: [mk('a', 'ok')] })
    const c = await mount()
    expect(q(c, 'health-overall').textContent).toBe('Everything looks good')
  })

  it('shows an error when the page cannot load', async () => {
    api.systemHealth.mockRejectedValue(new Error('boom'))
    const c = await mount()
    expect(c.textContent).toContain('boom')
  })
})

describe('Run history card', () => {
  const click = async (el) => {
    await act(async () => {
      el.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })
    await flush()
  }
  it('is off by default, says what could be tidied, and Tidy now is disabled', async () => {
    const c = await mount()
    const card = q(c, 'health-run-history')
    expect(card.textContent).toContain('Tidy old run history')
    expect(card.textContent).toContain('Keep full history for:')
    expect(q(c, 'tidy-preview').textContent).toBe('12 tasks could be tidied (about 12 MB of history)')
    expect(q(c, 'tidy-note').textContent).toBe("The database reuses this space; the disk file doesn't shrink.")
    expect(q(c, 'tidy-switch').getAttribute('aria-checked')).toBe('false')
    expect(q(c, 'tidy-now').disabled).toBe(true)
    expect(card.textContent).not.toMatch(/\{|retention|payload/)
  })
  it('the switch saves, and shows the last run', async () => {
    api.saveRetention.mockResolvedValue({ ...RET, enabled: true, last_run: '2026-11-20T10:00:00+00:00', last_count: 3 })
    const c = await mount()
    await click(q(c, 'tidy-switch'))
    expect(api.saveRetention).toHaveBeenCalledWith({ enabled: true })
    expect(q(c, 'tidy-last').textContent).toBe('Last tidied 20 Nov 2026: 3 tasks')
    expect(q(c, 'tidy-now').disabled).toBe(false)
  })
  it('Tidy now asks first, posts, and polls until it is not running', async () => {
    vi.useFakeTimers()
    try {
      api.retention.mockResolvedValue({ ...RET, enabled: true })
      api.runRetention.mockResolvedValue({ running: true })
      const c = await mount()
      await click(q(c, 'tidy-now'))
      expect(document.body.textContent).toContain(
        "Tidy now? Older run history of 12 finished tasks will be replaced by short summaries. This can't be undone.",
      )
      expect(api.runRetention).not.toHaveBeenCalled()
      api.retention.mockResolvedValueOnce({ ...RET, enabled: true, running: true })
      api.retention.mockResolvedValue({ ...RET, enabled: true, tasks: 0, last_run: '2026-11-25T10:00:00+00:00', last_count: 12 })
      const ok = [...document.querySelectorAll('.ant-popover button')].find((b) => b.textContent.trim() === 'Tidy now')
      await click(ok)
      expect(api.runRetention).toHaveBeenCalledTimes(1)
      await act(async () => {
        await vi.advanceTimersByTimeAsync(2500)
      })
      await flush()
      expect(q(c, 'tidy-last').textContent).toBe('Last tidied 25 Nov 2026: 12 tasks')
    } finally {
      vi.useRealTimers()
    }
  })
})

describe('re-clone warning', () => {
  it('shows "This is expected" for each folder and refreshes after it', async () => {
    api.systemHealth.mockResolvedValue({
      checked_at: DATA.checked_at,
      overall: 'attention',
      checks: [mk('repo_roots', 'attention', { summary: 'Shop: the folder now holds a different repository than before', detail: { projects: [{ id: 'p1', project: 'Shop' }] } })],
    })
    api.acceptRootCommit.mockResolvedValue({ ok: true })
    const c = await mount()
    const btn = q(c, 'reclone-accept-p1')
    expect(btn.textContent).toBe('This is expected')
    await act(async () => {
      btn.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })
    await flush()
    expect(api.acceptRootCommit).toHaveBeenCalledWith('p1')
    expect(api.systemHealth).toHaveBeenLastCalledWith(true)
  })
})

