import { act } from 'react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { flush, render } from '../home/testRender'

vi.mock('../api', () => ({ api: { getCodeMap: vi.fn(), getCodeMapLog: vi.fn(), refreshCodeMap: vi.fn(), updateProject: vi.fn() } }))
import { api } from '../api'
import CodeMapSection from './CodeMapSection'

const st = (o = {}) => ({ enabled: true, server_registered: true, status: 'ok', sha: 'abc1234def', behind: 0, error: null, indexed_at: null, next_try_at: null, ...o })
const q = (id) => document.querySelector(`[data-testid="${id}"]`)
const click = (el) => act(async () => el.dispatchEvent(new MouseEvent('click', { bubbles: true })))
let view
const mount = async (props = {}) => {
  view = await render(<CodeMapSection project={{ id: 'p1' }} canEdit {...props} />)
  await flush()
}
beforeAll(() => {
  window.matchMedia ||= () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} })
})
beforeEach(() => {
  vi.clearAllMocks()
  api.getCodeMap.mockResolvedValue(st())
  api.getCodeMapLog.mockResolvedValue({ lines: ['indexed 10 files'] })
  api.updateProject.mockResolvedValue({})
})
afterEach(async () => {
  await view?.unmount()
  view = null
  document.body.innerHTML = ''
})

describe('CodeMapSection polling', () => {
  afterEach(() => vi.useRealTimers())
  const tick = (ms) => act(async () => { await vi.advanceTimersByTimeAsync(ms) })
  it('polls every 5 s while running, status only, and stops when done', async () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] })
    api.getCodeMap.mockResolvedValue(st({ status: 'running', sha: null }))
    await mount()
    expect(api.getCodeMap).toHaveBeenCalledTimes(1)
    await tick(4900)
    expect(api.getCodeMap).toHaveBeenCalledTimes(1)
    await tick(200)
    expect(api.getCodeMap).toHaveBeenCalledTimes(2)
    expect(api.getCodeMapLog).not.toHaveBeenCalled()
    api.getCodeMap.mockResolvedValue(st())
    await tick(5000)
    const n = api.getCodeMap.mock.calls.length
    await tick(15000)
    expect(api.getCodeMap).toHaveBeenCalledTimes(n)
  })
  it('does not poll on an inactive tab or a hidden page', async () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] })
    api.getCodeMap.mockResolvedValue(st({ status: 'running', sha: null }))
    await mount({ active: false })
    await tick(20000)
    expect(api.getCodeMap).toHaveBeenCalledTimes(1)
    await view.unmount()
    await mount()
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => true })
    await tick(20000)
    expect(api.getCodeMap).toHaveBeenCalledTimes(2)
    delete document.hidden
  })
  it('keeps the raw reason under Details, hidden characters revealed', async () => {
    api.getCodeMap.mockResolvedValue(st({ status: 'failed', failure: 'error', error: 'exited with code 1: bad\u202Etxt' }))
    await mount()
    expect(q('codemap-line').textContent).toBe('Last refresh failed — the tool stopped with an error')
    expect(q('codemap-reason').textContent).toContain('⟦U+202E⟧')
    expect(q('codemap-reason').textContent).toContain('exited with code 1')
    expect(q('codemap-details').contains(q('codemap-reason'))).toBe(true)
  })
})

describe('CodeMapSection', () => {
  it('off: only the switch and the one-line explanation', async () => {
    api.getCodeMap.mockResolvedValue(st({ enabled: false }))
    await mount()
    expect(document.body.textContent).toContain("Keep a map of this project's code")
    expect(document.body.textContent).toContain('Lets tasks look things up in a map of the code instead of reading every file.')
    expect(q('codemap-status')).toBeNull()
  })
  it('shows the status line and details', async () => {
    await mount()
    expect(q('codemap-line').textContent).toBe('Up to date')
    expect(q('codemap-details').textContent).toContain('abc1234')
    expect(api.getCodeMapLog).not.toHaveBeenCalled()
  })
  it('missing tool links to Connected tools', async () => {
    api.getCodeMap.mockResolvedValue(st({ server_registered: false }))
    await mount()
    expect(q('codemap-line').textContent).toBe('Add the Code map tool first')
    expect(q('codemap-tools-link').getAttribute('href')).toBe('#/mcp')
  })
  it('the switch updates the project', async () => {
    api.getCodeMap.mockResolvedValue(st({ enabled: false }))
    await mount()
    await click(q('codemap-switch'))
    expect(api.updateProject).toHaveBeenCalledWith('p1', { code_map: true })
  })
  it('refresh calls the endpoint; a 403 reads plainly', async () => {
    api.refreshCodeMap.mockResolvedValueOnce(st({ status: 'running', sha: null }))
    await mount()
    await click(q('codemap-refresh'))
    expect(api.refreshCodeMap).toHaveBeenCalledWith('p1')
    expect(q('codemap-line').textContent).toBe('Building…')
    api.refreshCodeMap.mockRejectedValueOnce(Object.assign(new Error('Forbidden'), { status: 403 }))
    api.getCodeMap.mockResolvedValue(st())
    view.unmount && (await view.unmount())
    await mount()
    await click(q('codemap-refresh'))
    expect(q('codemap-error').textContent).toBe('You do not have permission to do that here.')
  })
  it('hides switch control actions from non-maintainers', async () => {
    await mount({ canEdit: false })
    expect(q('codemap-refresh')).toBeNull()
    expect(q('codemap-switch').disabled).toBe(true)
  })
  it('an external-trust 403 shows the server text', async () => {
    api.getCodeMap.mockResolvedValue(st({ enabled: false }))
    api.updateProject.mockRejectedValueOnce(Object.assign(new Error('Only an admin can turn the code map on for outside code'), { status: 403 }))
    await mount()
    await click(q('codemap-switch'))
    expect(q('codemap-error').textContent).toBe('Only an admin can turn the code map on for outside code')
  })
})
