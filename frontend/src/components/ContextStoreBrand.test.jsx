import { act } from 'react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { flush, render } from '../home/testRender'

vi.mock('../api', () => ({
  api: { listContextDocs: vi.fn(), previewContext: vi.fn(), brandGuideDoc: vi.fn(), updateContextDoc: vi.fn(), createContextDoc: vi.fn(), deleteContextDoc: vi.fn() },
}))
import { api } from '../api'
import ContextStoreView from './ContextStoreView'

const projects = [{ id: 'p1', name: 'Shop', my_role: 'maintainer' }]
const q = (id) => document.querySelector(`[data-testid="${id}"]`)
const click = (el) => act(async () => el.dispatchEvent(new MouseEvent('click', { bubbles: true })))
let view
const mount = async (props = {}) => {
  view = await render(<ContextStoreView projects={projects} isAdmin={false} {...props} />)
  await flush()
}
beforeAll(() => {
  window.matchMedia ||= () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} })
})
beforeEach(() => {
  vi.clearAllMocks()
  api.listContextDocs.mockResolvedValue([])
})
afterEach(async () => {
  await view?.unmount()
  view = null
  document.body.innerHTML = ''
})
const start = async () => {
  await click(q('brand-guide-start'))
  await flush()
  await click(q('brand-guide-create'))
  await flush()
}

describe('Brand guide template', () => {
  it('creates the guide for the only project and opens it', async () => {
    api.brandGuideDoc.mockResolvedValue({ id: 'd1', path: 'brand/brand.md', title: 'Brand guide', scope: 'project', project_id: 'p1', body: 'x', abstract: '', enabled: true })
    await mount()
    await start()
    expect(api.brandGuideDoc).toHaveBeenCalledWith('p1')
    expect(document.body.textContent).toContain('Edit brand/brand.md')
  })
  it('says so when the project already has one', async () => {
    api.brandGuideDoc.mockRejectedValue(Object.assign(new Error('c'), { status: 409 }))
    await mount()
    await start()
    expect(q('brand-guide-error').textContent).toContain('This project already has a brand guide.')
  })
  it('is hidden for people who cannot create docs', async () => {
    await mount({ projects: [{ id: 'p1', name: 'Shop', my_role: 'viewer' }] })
    expect(q('brand-guide-start')).toBeNull()
  })
})
