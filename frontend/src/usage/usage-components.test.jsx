import { act } from 'react'
import { afterEach, beforeAll, describe, expect, it } from 'vitest'
import { flush, render } from '../home/testRender'
import UsageChips from './UsageChips'
import SoFarLine from './SoFarLine'
import TokensPanel from './TokensPanel'

const q = (c, id) => c.querySelector(`[data-testid="${id}"]`)
const click = (el) => act(async () => el.dispatchEvent(new MouseEvent('click', { bubbles: true })))

const bucket = (messages, input, cache_write, cache_read, output) => ({ messages, input, cache_write, cache_read, output })
const report = (over = {}) => ({
  step: {
    context_tokens: 63400,
    peak_context: 70100,
    est_cost_usd: 0.82,
    cost_is_estimate: true,
    max_budget_usd: 2,
    budget_pct: 41,
    fresh_session_tokens: null,
    breakdown: {
      main: bucket(12, 3400, 21000, 180000, 5200),
      helpers: { Explore: bucket(4, 900, 3000, 12000, 800) },
      models: { 'claude-sonnet-4-5': bucket(16, 4300, 24000, 192000, 6000) },
      largest_results: [{ tool: 'Read', chars: 48213, id: 't1' }, { tool: 'Bash‮', chars: 21000, id: 't2' }],
      injected_chars: 1234,
      messages: 16,
    },
    ...over,
  },
  thread: { steps: 2, duration_ms: 41 * 60000, cost_usd: 2.1, tokens: 1_200_000, last: { title: 'Step two', duration_ms: 18 * 60000, cost_usd: 1.4 } },
  tips: ['A tool result over 20,000 characters came back; read narrower ranges or filter output.'],
})

let view
beforeAll(() => {
  window.matchMedia ||= () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} })
})
afterEach(async () => {
  await view?.unmount()
  view = null
})
const mount = async (el) => {
  view = await render(el)
  await flush()
  return view.container
}

describe('UsageChips', () => {
  it('shows the context chip without colour when there is no threshold, and the budget estimate', async () => {
    const c = await mount(<UsageChips report={report()} />)
    expect(q(c, 'usage-context').textContent).toBe('context 63k')
    expect(q(c, 'usage-context').className).toContain('co-usage-chip-none')
    expect(q(c, 'usage-budget').textContent).toBe('budget 41 % est.')
  })
  it('colours the context chip amber then red against the threshold', async () => {
    let c = await mount(<UsageChips report={report({ fresh_session_tokens: 75000 })} />)
    expect(q(c, 'usage-context').className).toContain('co-usage-chip-amber')
    await view.unmount()
    c = await mount(<UsageChips report={report({ context_tokens: 151000, fresh_session_tokens: 150000 })} />)
    expect(q(c, 'usage-context').className).toContain('co-usage-chip-red')
  })
  it('follows the server: "est." for a finished step without a result cost, none with one', async () => {
    let c = await mount(<UsageChips report={report({ cost_is_estimate: true })} />)
    expect(q(c, 'usage-budget').textContent).toBe('budget 41 % est.')
    await view.unmount()
    c = await mount(<UsageChips report={report({ cost_is_estimate: false })} />)
    expect(q(c, 'usage-budget').textContent).toBe('budget 41 %')
  })
  it('hides the budget chip without a max budget', async () => {
    const c = await mount(<UsageChips report={report({ max_budget_usd: null, budget_pct: null })} />)
    expect(q(c, 'usage-budget')).toBeNull()
    expect(q(c, 'usage-context')).not.toBeNull()
  })
  it('renders nothing without a report or figures', async () => {
    let c = await mount(<UsageChips report={null} />)
    expect(c.textContent).toBe('')
    await view.unmount()
    c = await mount(<UsageChips report={report({ context_tokens: null, max_budget_usd: null, budget_pct: null })} />)
    expect(q(c, 'usage-chips')).toBeNull()
  })
})

describe('SoFarLine', () => {
  it('shows the thread totals', async () => {
    const c = await mount(<SoFarLine thread={report().thread} />)
    expect(q(c, 'usage-so-far').textContent).toContain('2 steps · 41 min · $2.10 · 1.2M tokens — last: Step two, 18 min, $1.40')
  })
  it('renders nothing without a thread', async () => {
    const c = await mount(<SoFarLine thread={null} />)
    expect(q(c, 'usage-so-far')).toBeNull()
  })
})

describe('TokensPanel', () => {
  it('starts collapsed and opens to buckets, models, largest results, injected text and tips', async () => {
    const c = await mount(<TokensPanel report={report()} />)
    expect(q(c, 'tokens-panel').textContent).toContain('Where the tokens went')
    expect(q(c, 'tokens-body')).toBeNull()
    await click(q(c, 'tokens-toggle'))
    const body = q(c, 'tokens-body')
    const rows = [...body.querySelectorAll('[data-testid="tokens-bucket"]')].map((r) => r.textContent)
    expect(rows[0]).toContain('main')
    expect(rows[0]).toContain('180,000')
    expect(rows[1]).toContain('helper: Explore')
    expect(rows[1]).toContain('12,000')
    expect(q(c, 'tokens-models').textContent).toContain('claude-sonnet-4-5')
    const big = [...body.querySelectorAll('[data-testid="tokens-largest"] li')].map((l) => l.textContent)
    expect(big[0]).toContain('Read')
    expect(big[0]).toContain('48,213 chars')
    expect(big[1]).toContain('Bash⟦U+202E⟧')
    expect(q(c, 'tokens-injected').textContent).toContain('1,234')
    expect(q(c, 'tokens-tips').textContent).toContain('read narrower ranges')
    await click(q(c, 'tokens-toggle'))
    expect(q(c, 'tokens-body')).toBeNull()
  })
  it('shows only the tips when there is no breakdown yet', async () => {
    const c = await mount(<TokensPanel report={report({ breakdown: null })} />)
    await click(q(c, 'tokens-toggle'))
    expect(q(c, 'tokens-bucket')).toBeNull()
    expect(q(c, 'tokens-tips')).not.toBeNull()
  })
  it('renders nothing without a breakdown or tips', async () => {
    const r = report({ breakdown: null })
    r.tips = []
    const c = await mount(<TokensPanel report={r} />)
    expect(q(c, 'tokens-panel')).toBeNull()
  })
})
