import { act } from 'react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { flush, render } from '../home/testRender'
import ScopeNudge from './ScopeNudge'

let view
beforeAll(() => {
  window.matchMedia ||= () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} })
})
afterEach(async () => {
  await view?.unmount()
  view = null
})

describe('ScopeNudge', () => {
  it('shows the exact copy and three buttons that call back', async () => {
    const f = { onAdd: vi.fn(), onAfter: vi.fn(), onRunAnyway: vi.fn() }
    view = await render(<ScopeNudge open={4} wouldBe={7} {...f} />)
    await flush()
    const c = view.container
    expect(c.querySelector('[data-testid="scope-nudge-text"]').textContent).toBe('This looks like new scope: the launch list has 4 open items; this would become #7.')
    const btns = [...c.querySelectorAll('button')].map((b) => b.textContent.replace(/\s/g, ' '))
    expect(btns).toEqual(['Add to launch list', 'After launch', 'Run anyway'])
    for (const id of ['nudge-add', 'nudge-after', 'nudge-run']) await act(async () => c.querySelector(`[data-testid="${id}"]`).click())
    expect(f.onAdd).toHaveBeenCalledTimes(1)
    expect(f.onAfter).toHaveBeenCalledTimes(1)
    expect(f.onRunAnyway).toHaveBeenCalledTimes(1)
  })
})
