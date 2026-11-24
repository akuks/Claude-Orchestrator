import { afterEach, describe, expect, it } from 'vitest'
import { render } from '../home/testRender'
import { breakable } from './breakable'

let view
afterEach(async () => {
  await view?.unmount()
  view = null
})
describe('breakable', () => {
  it('adds a break after each underscore and keeps the text', async () => {
    view = await render(<span>{breakable('a_b_c')}</span>)
    expect(view.container.querySelectorAll('wbr')).toHaveLength(2)
    expect(view.container.textContent).toBe('a_b_c')
  })
  it('keeps hostile text as text', async () => {
    view = await render(<span>{breakable('<b>x</b>_y')}</span>)
    expect(view.container.querySelector('b')).toBeNull()
    expect(view.container.textContent).toBe('<b>x</b>_y')
  })
})
