import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { flush, render } from '../home/testRender'

vi.mock('../api', () => ({ api: { getGitState: vi.fn() } }))
import { api } from '../api'
import { GitStateWarning } from './ProjectsView'

let view
beforeAll(() => {
  window.matchMedia ||= () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} })
})
beforeEach(() => vi.clearAllMocks())
afterEach(async () => {
  await view?.unmount()
  view = null
})
const warning = () => view.container.querySelector('[data-testid="no-commits-warning"]')
const mount = async () => {
  view = await render(<GitStateWarning projectId="p1" />)
  await flush()
  await flush()
}

describe('project drawer git-state warning', () => {
  it('shows for no_commits true', async () => {
    api.getGitState.mockResolvedValue({ is_repo: true, no_commits: true })
    await mount()
    expect(api.getGitState).toHaveBeenCalledWith('p1')
    expect(warning().textContent).toContain('No commits yet')
  })
  it('absent for false, and when the check fails', async () => {
    api.getGitState.mockResolvedValue({ is_repo: true, no_commits: false })
    await mount()
    expect(warning()).toBeNull()
    await view.unmount()
    api.getGitState.mockRejectedValue(new Error('x'))
    await mount()
    expect(warning()).toBeNull()
  })
})
