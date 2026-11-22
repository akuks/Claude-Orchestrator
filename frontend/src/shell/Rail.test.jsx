import { afterEach, beforeAll, describe, expect, it } from 'vitest'
import { render } from '../home/testRender'
import Rail from './Rail'

let view
beforeAll(() => {
  window.matchMedia ||= () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} })
})
afterEach(async () => {
  await view?.unmount()
  view = null
})

describe('Rail: open questions badge on Home', () => {
  it('shows the count of open questions the viewer may answer', async () => {
    view = await render(<Rail page="tasks" isAdmin={false} questionCount={3} />)
    const home = view.container.querySelector('[data-testid="rail-home"]')
    expect(home.querySelector('[data-testid="rail-questions-badge"]').textContent).toContain('3')
    expect(home.getAttribute('aria-label')).toBe('Home (3 open questions)')
  })
  it('no count, no badge number', async () => {
    view = await render(<Rail page="tasks" isAdmin={false} questionCount={0} />)
    const home = view.container.querySelector('[data-testid="rail-home"]')
    expect(home.querySelector('.ant-badge-count')).toBeNull()
    expect(home.getAttribute('aria-label')).toBe('Home')
  })
})
