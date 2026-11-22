import { afterEach, beforeAll, describe, expect, it } from 'vitest'
import { render } from '../home/testRender'
import RunNotices from './RunNotices'

let view
beforeAll(() => {
  window.matchMedia ||= () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} })
})
afterEach(async () => {
  await view?.unmount()
  view = null
})

describe('RunNotices', () => {
  it('renders one alert per notice, as plain text with hidden characters shown', async () => {
    const events = [
      { type: 'recovery', payload: { why: 'x<i>y</i>‮', at: 'a', in_flight: 0, orphan_killed: false, recoveries: 2, gave_up: true } },
      { type: 'fresh_session', payload: { reason: 'r', credentials_changed: true } },
    ]
    view = await render(<RunNotices task={{}} events={events} handoff={{ content: { no_commits: true } }} />)
    const c = view.container
    expect(c.querySelector('[data-testid="notice-gave_up"]').textContent).toContain('x<i>y</i>⟦U+202E⟧')
    expect(c.querySelector('i')).toBeNull()
    expect(c.querySelector('[data-testid="notice-credentials_changed"]')).not.toBeNull()
    expect(c.querySelector('[data-testid="notice-no_commits"]')).not.toBeNull()
  })
  it('renders nothing without notices', async () => {
    view = await render(<RunNotices task={{}} events={[]} handoff={null} />)
    expect(view.container.querySelector('[data-testid="run-notices"]')).toBeNull()
  })
})
