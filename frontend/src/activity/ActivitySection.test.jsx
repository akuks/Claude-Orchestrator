import { act } from 'react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { flush, render } from '../home/testRender'

vi.mock('../api', () => ({ api: { getActivity: vi.fn() } }))
import { api } from '../api'
import ActivitySection from './ActivitySection'
import { groupByDay, changeLine } from './activity'

const commit = (o = {}) => ({ sha: 'abcdef1234', short: 'abcdef1', date: '2026-11-24T10:00:00+00:00', author: 'Asha', title: 'Fix login', added: 12, removed: 3, files: 4, task: null, ...o })
const answer = (o = {}) => ({
  days: 30, is_repo: true,
  commits: [commit({ task: { id: 't9', title: 'Login work' } }), commit({ sha: 'b', short: 'b', date: '2026-11-23T10:00:00+00:00', title: 'Older', files: 1 })],
  by_day: [{ date: '2026-11-23', commits: 1, added: 1, removed: 0 }, { date: '2026-11-24', commits: 1, added: 12, removed: 3 }],
  contributors: [{ name: 'Asha', commits: 2, added: 13, removed: 3 }], truncated: false, ...o,
})
const q = (id) => document.querySelector(`[data-testid="${id}"]`)
let view
const mount = async (props = {}) => {
  view = await render(<ActivitySection project={{ id: 'p1' }} active {...props} />)
  await flush()
}
beforeAll(() => {
  window.matchMedia ||= () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} })
})
beforeEach(() => {
  vi.clearAllMocks()
  api.getActivity.mockResolvedValue(answer())
})
afterEach(async () => {
  await view?.unmount()
  view = null
  document.body.innerHTML = ''
})

describe('helpers', () => {
  it('words the change line', () => {
    expect(changeLine({ added: 12, removed: 3, files: 4 })).toBe('+12 −3 in 4 files')
    expect(changeLine({ added: 1, removed: 0, files: 1 })).toBe('+1 −0 in 1 file')
  })
  it('groups by day, newest first', () => {
    const g = groupByDay(answer().commits)
    expect(g.map((x) => x.date)).toEqual(['2026-11-24', '2026-11-23'])
  })
})

describe('ActivitySection', () => {
  it('does not fetch until active', async () => {
    await mount({ active: false })
    expect(api.getActivity).not.toHaveBeenCalled()
  })
  it('loads 30 days and shows commits, task link, contributors', async () => {
    await mount()
    expect(api.getActivity).toHaveBeenCalledWith('p1', 30, 0)
    expect(document.body.textContent).toContain('Fix login')
    expect(document.body.textContent).toContain('+12 −3 in 4 files')
    const a = q('activity-task-link')
    expect(a.getAttribute('href')).toBe('#/tasks/t9')
    expect(a.textContent).toBe('Made by task Login work')
    expect(q('activity-contributors').textContent).toContain('Asha')
    expect(q('activity-chart')).toBeTruthy()
  })
  it('asks for bigger changes only', async () => {
    await mount()
    await act(async () => q('activity-bigger').click())
    await flush()
    expect(api.getActivity).toHaveBeenLastCalledWith('p1', 30, 50)
  })
  it('shows plain empty and not-a-repo messages', async () => {
    api.getActivity.mockResolvedValue(answer({ commits: [], by_day: [], contributors: [] }))
    await mount()
    expect(document.body.textContent).toContain('No commits in this period.')
    await view.unmount()
    api.getActivity.mockResolvedValue(answer({ is_repo: false, commits: [] }))
    await mount()
    expect(document.body.textContent).toContain("This project's folder isn't a git repository.")
  })
  it('says when the list is cut short', async () => {
    api.getActivity.mockResolvedValue(answer({ truncated: true }))
    await mount()
    expect(document.body.textContent).toContain('Showing the newest 2,000 commits.')
  })
})
