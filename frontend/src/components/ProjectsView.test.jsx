import { act } from 'react'
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { flush, render } from '../home/testRender'

vi.mock('../api', () => ({
  api: {
    getGitState: vi.fn(),
    getProjectMembers: vi.fn(async () => []),
    userDirectory: vi.fn(async () => []),
    listTeams: vi.fn(async () => []),
    updateProject: vi.fn(async (id, v) => ({ id, ...v })),
  },
}))
import { api } from '../api'
import { GitStateWarning, ProjectModal } from './ProjectsView'

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

describe('project form: fresh-session threshold (phase 3b)', () => {
  const project = (over = {}) => ({ id: 'p1', name: 'P', default_model: 'sonnet', my_role: 'maintainer', fresh_session_tokens: null, ...over })
  const field = () => document.querySelector('input[data-testid="fresh-session-input"]')
  const ok = () => [...document.querySelectorAll('.ant-modal-footer button')].find((b) => b.textContent.trim() === 'OK')
  const click = (el) => act(async () => el.dispatchEvent(new MouseEvent('click', { bubbles: true })))
  async function type(el, value) {
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set
      setter.call(el, value)
      el.dispatchEvent(new Event('input', { bubbles: true }))
    })
  }
  const settle = async () => {
    for (let i = 0; i < 5; i++) await flush()
    await act(async () => new Promise((r) => setTimeout(r, 0)))
  }
  // antd's modal measures the scrollbar with a pseudo-element, which jsdom does not implement
  const realStyle = window.getComputedStyle
  beforeAll(() => {
    window.getComputedStyle = (el) => realStyle(el)
  })
  afterAll(() => {
    window.getComputedStyle = realStyle
  })
  const open = async (p) => {
    view = await render(<ProjectModal open project={p} isAdmin={false} currentUser={{ id: 'u1' }} onClose={() => {}} />)
    await settle()
  }

  it('is off (empty) with the 150000 suggestion, and saves off as null', async () => {
    await open(project())
    expect(field().value).toBe('')
    expect(field().getAttribute('placeholder')).toBe('150000')
    await click(ok())
    await settle()
    expect(api.updateProject).toHaveBeenCalledWith('p1', expect.objectContaining({ fresh_session_tokens: null }))
  })

  it('shows the stored value and saves a valid number', async () => {
    await open(project({ fresh_session_tokens: 200000 }))
    expect(field().value).toBe('200000')
    await type(field(), '150000')
    await click(ok())
    await settle()
    expect(api.updateProject).toHaveBeenCalledWith('p1', expect.objectContaining({ fresh_session_tokens: 150000 }))
  })

  it('refuses a value outside 50,000 to 900,000', async () => {
    await open(project())
    await type(field(), '40000')
    await click(ok())
    await settle()
    expect(api.updateProject).not.toHaveBeenCalled()
    expect(document.querySelector('[data-testid="fresh-session"]').textContent).toMatch(/50,000 to 900,000/)
  })

  it('is not offered to a non-maintainer, nor on a new project', async () => {
    await open(project({ my_role: 'contributor' }))
    expect(field()).toBeNull()
    await view.unmount()
    view = await render(<ProjectModal open project={null} isAdmin={false} currentUser={{ id: 'u1' }} onClose={() => {}} />)
    await settle()
    expect(field()).toBeNull()
  })
})
