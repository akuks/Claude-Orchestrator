import { act } from 'react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { flush, render } from '../home/testRender'

vi.mock('../api', () => ({ api: { updateProject: vi.fn(), createServer: vi.fn(), setupUiChecks: vi.fn() } }))
import { api } from '../api'
import RecommendedSection from './RecommendedSection'

const preset = { key: 'browser-playwright', name: 'Browser (Playwright)', description: 'd', transport: 'stdio', command: 'npx', args: ['-y', '@playwright/mcp@0.0.86'], pinned: '0.0.86' }
const rec = (o = {}) => ({
  has_ui: true, react: true, ui_skills: true, brand_skill: null, brand_skill_found: false,
  items: [
    { key: 'browser-playwright', name: 'Browser (Playwright)', why: 'Lets an agent open your pages.', kind: 'tool', present: false, preset },
    { key: 'browser-devtools', name: 'Browser inspector (DevTools)', why: 'Inspect a page.', kind: 'tool', present: true, preset: { ...preset, key: 'browser-devtools' } },
    { key: 'frontend-design', name: 'Frontend design skill', why: 'Makes things look designed.', kind: 'skill', present: false, preset: null },
  ],
  ...o,
})
const q = (id) => document.querySelector(`[data-testid="${id}"]`)
const click = (el) => act(async () => el.dispatchEvent(new MouseEvent('click', { bubbles: true })))
let view
let reload
const mount = async (props = {}) => {
  reload = vi.fn()
  view = await render(<RecommendedSection project={{ id: 'p1', name: 'Shop' }} data={rec()} reload={reload} canEdit isAdmin {...props} />)
  await flush()
}
beforeAll(() => {
  window.matchMedia ||= () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} })
})
beforeEach(() => {
  vi.clearAllMocks()
  api.updateProject.mockResolvedValue({})
  api.createServer.mockResolvedValue({})
})
afterEach(async () => {
  await view?.unmount()
  view = null
  document.body.innerHTML = ''
})

describe('RecommendedSection', () => {
  it('shows Added, an Add button, and the skills hint', async () => {
    await mount()
    expect(q('rec-item-browser-devtools').textContent).toContain('Added')
    expect(q('rec-add-browser-devtools')).toBeNull()
    expect(q('rec-add-browser-playwright')).not.toBeNull()
    expect(q('rec-item-frontend-design').textContent).toContain('Add it under Skills')
    expect(q('rec-add-frontend-design')).toBeNull()
  })
  it('non-admins are told to ask an admin', async () => {
    await mount({ isAdmin: false })
    expect(q('rec-add-browser-playwright')).toBeNull()
    expect(q('rec-item-browser-playwright').textContent).toContain('Ask an admin to add this')
  })
  it('Add asks first, then posts the preset scoped to the project and reloads', async () => {
    await mount()
    await click(q('rec-add-browser-playwright'))
    expect(document.body.textContent).toContain('Add Browser (Playwright) to this project?')
    expect(api.createServer).not.toHaveBeenCalled()
    await click(document.querySelector('.ant-popconfirm .ant-btn-primary'))
    await flush()
    expect(api.createServer).toHaveBeenCalledWith(expect.objectContaining({
      name: 'Browser (Playwright) (Shop)', transport: 'stdio', command: 'npx', args: ['-y', '@playwright/mcp@0.0.86'], scope: 'project', project: 'Shop',
    }))
    expect(reload).toHaveBeenCalled()
  })
  it('names the tool per project and says plainly when it is already added', async () => {
    api.createServer.mockRejectedValue(Object.assign(new Error("MCP server 'x' already exists"), { status: 409 }))
    await mount()
    await click(q('rec-add-browser-playwright'))
    await click(document.querySelector('.ant-popconfirm .ant-btn-primary'))
    await flush()
    expect(q('rec-item-browser-playwright').textContent).toContain('A tool with this name already exists under Connected tools. Switch it on there.')
    expect(q('rec-item-browser-playwright').textContent).not.toContain('MCP server')
  })
  it('the switch saves ui_skills through updateProject', async () => {
    await mount()
    await click(q('rec-ui-skills'))
    await flush()
    expect(api.updateProject).toHaveBeenCalledWith('p1', { ui_skills: false })
    expect(reload).toHaveBeenCalled()
  })
  it('the brand field saves, and a 422 message shows under it', async () => {
    await mount()
    const input = q('rec-brand')
    const type = (v) => act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, v)
      input.dispatchEvent(new Event('input', { bubbles: true }))
    })
    await type('acme-brand')
    await click(q('rec-brand-save'))
    await flush()
    expect(api.updateProject).toHaveBeenCalledWith('p1', { brand_skill: 'acme-brand' })
    api.updateProject.mockRejectedValue(Object.assign(new Error('brand_skill: use letters and digits only'), { status: 422 }))
    await type('bad name')
    await click(q('rec-brand-save'))
    await flush()
    expect(q('rec-brand-error').textContent).toBe('Use letters, numbers, dots, dashes, underscores or colons only (up to 120).')
  })
  it('a 403 reads as a plain sentence', async () => {
    api.updateProject.mockRejectedValue(Object.assign(new Error('403 Forbidden'), { status: 403 }))
    await mount()
    await click(q('rec-ui-skills'))
    await flush()
    expect(document.body.textContent).toContain("You don't have permission to change this.")
    expect(document.body.textContent).not.toContain('403')
  })
  it('read-only for people who cannot edit', async () => {
    await mount({ canEdit: false })
    expect(q('rec-brand-save')).toBeNull()
  })

  it('tags a tool that needs a newer Node', async () => {
    const d = rec()
    d.items[0] = { ...d.items[0], mark: 'Needs Node 20' }
    await mount({ data: d })
    expect(q('rec-item-browser-playwright').textContent).toContain('Needs Node 20')
    expect(q('rec-item-browser-devtools').textContent).not.toContain('Needs Node')
  })

  it('starts the UI checks task and links to it', async () => {
    api.setupUiChecks.mockResolvedValue({ id: 'T9', title: 'Set up UI checks' })
    await mount()
    expect(q('rec-ui-checks').textContent).toContain('screenshot, accessibility and Lighthouse')
    await click(q('rec-ui-checks-setup'))
    await flush()
    const ok = [...document.querySelectorAll('.ant-popover button')].find((b) => /Start/.test(b.textContent))
    await click(ok)
    await flush()
    expect(api.setupUiChecks).toHaveBeenCalledWith('p1')
    expect(q('rec-ui-checks-done').textContent).toContain('Task started')
    expect(q('rec-ui-checks-link').getAttribute('href')).toBe('#/tasks/T9')
  })

  it('says it plainly when setup fails', async () => {
    api.setupUiChecks.mockRejectedValue(Object.assign(new Error('x'), { status: 500 }))
    await mount()
    await click(q('rec-ui-checks-setup'))
    await flush()
    await click([...document.querySelectorAll('.ant-popover button')].find((b) => /Start/.test(b.textContent)))
    await flush()
    expect(q('rec-ui-checks-error').textContent).toContain("didn't work")
  })

  it('asks non-maintainers to get a maintainer', async () => {
    await mount({ canEdit: false })
    expect(q('rec-ui-checks-setup')).toBeNull()
    expect(q('rec-ui-checks').textContent).toContain('Ask a maintainer to set up UI checks.')
  })
})
