import { act } from 'react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { flush, render } from '../home/testRender'

vi.mock('../api', () => ({ api: { systemHealth: vi.fn() } }))

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

let view
beforeAll(() => {
  window.matchMedia ||= () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} })
})
beforeEach(() => {
  vi.clearAllMocks()
  api.systemHealth.mockResolvedValue(DATA)
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
