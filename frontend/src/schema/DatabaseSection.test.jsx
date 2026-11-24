import { act } from 'react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { flush, render } from '../home/testRender'

vi.mock('../api', () => ({ api: { getSchema: vi.fn() } }))
import { api } from '../api'
import DatabaseSection from './DatabaseSection'

const col = (name, o = {}) => ({ name, type: 'int', nullable: false, primary_key: false, defined_at: 'migrations/001.sql:3', ...o })
const answer = (o = {}) => ({
  found: true,
  roots: [{
    root: 'migrations', files: 2,
    tables: [
      { name: 'customers', uncertain: false, defined_at: 'migrations/001.sql:1', columns: [col('id', { primary_key: true }), col('name', { type: 'text' })], links: [], decisions: [{ id: 'D-004', title: 'Keep customers unique' }] },
      { name: 'orders', uncertain: true, defined_at: 'migrations/002.sql:1', columns: [col('id', { primary_key: true }), col('customer_id')], links: [{ columns: ['customer_id'], to_table: 'customers', to_columns: ['id'], defined_at: 'migrations/002.sql:4' }], decisions: [] },
    ],
  }],
  as_of: 'abc1234def', not_understood: { count: 0, samples: [] }, limits: [], note: 'Worked out by reading the SQL migration files in order.', ...o,
})
const q = (id) => document.querySelector(`[data-testid="${id}"]`)
const qa = (id) => [...document.querySelectorAll(`[data-testid="${id}"]`)]
const click = (el) => act(async () => el.dispatchEvent(new MouseEvent('click', { bubbles: true })))
let view
const mount = async (props = {}) => {
  view = await render(<DatabaseSection project={{ id: 'p1' }} active {...props} />)
  await flush()
}
beforeAll(() => {
  window.matchMedia ||= () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} })
  Element.prototype.scrollIntoView ||= () => {}
})
beforeEach(() => {
  vi.clearAllMocks()
  api.getSchema.mockResolvedValue(answer())
})
afterEach(async () => {
  await view?.unmount()
  view = null
  document.body.innerHTML = ''
})

describe('DatabaseSection', () => {
  it('fetches only when the tab is active', async () => {
    await mount({ active: false })
    expect(api.getSchema).not.toHaveBeenCalled()
  })
  it('shows a card per table with key marks and a link chip', async () => {
    await mount()
    expect(qa('db-table')).toHaveLength(2)
    const orders = qa('db-table')[1]
    expect(orders.querySelectorAll('[data-testid="db-key"]')).toHaveLength(1)
    const chip = orders.querySelector('[data-testid="db-link-chip"]')
    expect(chip.textContent).toContain('customers')
    expect(chip.getAttribute('aria-label')).toBe('links to customers')
    expect(orders.textContent).toContain('Links to customers')
  })
  it('clicking a chip scrolls to and highlights the linked table', async () => {
    const spy = vi.spyOn(Element.prototype, 'scrollIntoView').mockImplementation(() => {})
    await mount()
    await click(q('db-link-chip'))
    expect(spy).toHaveBeenCalled()
    expect(qa('db-table')[0].className).toContain('co-db-hit')
    spy.mockRestore()
  })
  it('says an uncertain table may differ', async () => {
    await mount()
    expect(qa('db-table')[1].textContent).toContain('May differ from what is shown')
    expect(qa('db-table')[0].textContent).not.toContain('May differ')
  })
  it('keeps file positions and the sha under Details only', async () => {
    await mount()
    const d = q('db-details')
    const outside = document.body.textContent.replace(d.textContent, '')
    expect(outside).not.toMatch(/\.sql(:\d|, line)/)
    expect(outside).not.toContain('migrations/')
    expect(outside).not.toContain('abc1234')
    expect(d.textContent).toContain('migrations/002.sql, line 1')
    expect(d.textContent).toContain('abc1234')
    expect(d.textContent).toContain('Keep customers unique')
    expect(d.hasAttribute('open')).toBe(false)
  })
  it('says how many statements were not understood, and the limits', async () => {
    api.getSchema.mockResolvedValue(answer({ not_understood: { count: 3, samples: ['migrations/009.sql:12'] }, limits: ['Only the first 60 tables are shown.'] }))
    await mount()
    expect(q('db-not-understood').textContent).toBe('3 statements were not understood')
    expect(q('db-limits').textContent).toContain('Only the first 60 tables are shown.')
    expect(q('db-details').textContent).toContain('migrations/009.sql, line 12')
  })
  it('one statement reads in the singular', async () => {
    api.getSchema.mockResolvedValue(answer({ not_understood: { count: 1, samples: [] } }))
    await mount()
    expect(q('db-not-understood').textContent).toBe('1 statement was not understood')
  })
  it('shows the note as the empty state, with Try again only when reading failed', async () => {
    api.getSchema.mockResolvedValue({ found: false, roots: [], note: 'No SQL migration files found.', limits: [], not_understood: { count: 0, samples: [] } })
    await mount()
    expect(q('db-empty').textContent).toContain('No SQL migration files found.')
    expect(q('db-retry')).toBeNull()
    expect(q('db-empty').textContent).toContain('not read')
    api.getSchema.mockResolvedValue({ found: true, roots: [], note: 'Reading the migration files took too long.', limits: [], not_understood: { count: 0, samples: [] } })
    await view.unmount()
    await mount()
    expect(q('db-empty').textContent).toContain('took too long')
    api.getSchema.mockResolvedValue(answer())
    await click(q('db-retry'))
    await flush()
    expect(qa('db-table')).toHaveLength(2)
  })
  it('shows a search box only above 8 tables and filters by name', async () => {
    await mount()
    expect(q('db-search')).toBeNull()
    const tables = Array.from({ length: 9 }, (_, i) => ({ name: `t${i}`, uncertain: false, defined_at: 'm/1.sql:1', columns: [col('id', { primary_key: true })], links: [], decisions: [] }))
    api.getSchema.mockResolvedValue(answer({ roots: [{ root: 'migrations', files: 1, tables }] }))
    await view.unmount()
    await mount()
    const input = q('db-search')
    expect(input).not.toBeNull()
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, 't3')
      input.dispatchEvent(new Event('input', { bubbles: true }))
    })
    expect(qa('db-table')).toHaveLength(1)
  })
  it('shows a plain message when the request fails', async () => {
    api.getSchema.mockRejectedValue(new Error('boom'))
    await mount()
    expect(q('db-error').textContent).toContain("That didn't work. Try again.")
    expect(q('db-error').textContent).not.toContain('boom')
  })
  it('renders a hostile table name as plain text', async () => {
    const bad = '<img src=x onerror=alert(1)>'
    api.getSchema.mockResolvedValue(answer({ roots: [{ root: 'migrations', files: 1, tables: [{ name: bad, uncertain: false, defined_at: 'm/1.sql:1', columns: [col('id')], links: [], decisions: [] }] }] }))
    await mount()
    expect(document.querySelector('[data-testid="db-table"] img')).toBeNull()
    expect(qa('db-table')[0].textContent).toContain(bad)
  })
  it('heads several migration folders as groups, folders only under Details', async () => {
    const r = (root) => ({ root, files: 1, tables: [{ name: `t_${root.length}`, uncertain: false, defined_at: 'm/1.sql:1', columns: [col('id')], links: [], decisions: [] }] })
    api.getSchema.mockResolvedValue(answer({ roots: [r('migrations'), r('db/migrations')] }))
    await mount()
    const main = q('db-section').textContent.replace(q('db-details').textContent, '')
    expect(main).toContain('Group 1')
    expect(main).toContain('Group 2')
    expect(main).not.toContain('db/migrations')
    expect(q('db-details').textContent).toContain('db/migrations/')
  })
  it('looks again when the tab is reopened', async () => {
    await mount()
    expect(api.getSchema).toHaveBeenCalledTimes(1)
    await view.rerender(<DatabaseSection project={{ id: 'p1' }} active={false} />)
    await view.rerender(<DatabaseSection project={{ id: 'p1' }} active />)
    await flush()
    expect(api.getSchema).toHaveBeenCalledTimes(2)
  })
})
