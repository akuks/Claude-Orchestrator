import { describe, expect, it } from 'vitest'
import {
  FRESH_MAX,
  FRESH_MIN,
  FRESH_SUGGEST,
  budgetChip,
  contextChip,
  fmtDuration,
  fmtTokens,
  freshSessionError,
  soFarLine,
  usageLine,
} from './usage'

describe('contextChip', () => {
  it('is hidden without a context figure', () => {
    expect(contextChip(null)).toBeNull()
    expect(contextChip({ context_tokens: null, fresh_session_tokens: 150000 })).toBeNull()
  })
  it('shows "context {n}k" with no colour when there is no threshold', () => {
    expect(contextChip({ context_tokens: 63400, fresh_session_tokens: null })).toEqual({
      text: 'context 63k',
      level: 'none',
    })
  })
  it('is amber at 80 % of the threshold and red at the threshold', () => {
    const at = (n) => contextChip({ context_tokens: n, fresh_session_tokens: 100000 }).level
    expect(at(79999)).toBe('none')
    expect(at(80000)).toBe('amber')
    expect(at(99999)).toBe('amber')
    expect(at(100000)).toBe('red')
    expect(at(250000)).toBe('red')
  })
})

describe('budgetChip', () => {
  const step = { max_budget_usd: 2, budget_pct: 41.2, est_cost_usd: 0.82, cost_is_estimate: true }
  it('says "budget {pct} % est." while the step runs', () => {
    expect(budgetChip(step, true)).toEqual({ text: 'budget 41 % est.', level: 'none' })
  })
  it('drops "est." after the finish', () => {
    expect(budgetChip({ ...step, cost_is_estimate: false }, false).text).toBe('budget 41 %')
    expect(budgetChip(step, false).text).toBe('budget 41 %')
  })
  it('is hidden without a max budget or an estimate', () => {
    expect(budgetChip(null, true)).toBeNull()
    expect(budgetChip({ ...step, max_budget_usd: null, budget_pct: null }, true)).toBeNull()
    expect(budgetChip({ ...step, est_cost_usd: null, budget_pct: null }, true)).toBeNull()
  })
  it('turns amber at 80 % and red at 95 %', () => {
    expect(budgetChip({ ...step, budget_pct: 80 }, true).level).toBe('amber')
    expect(budgetChip({ ...step, budget_pct: 95 }, true).level).toBe('red')
  })
})

describe('so far', () => {
  it('formats durations and tokens', () => {
    expect(fmtDuration(41 * 60000)).toBe('41 min')
    expect(fmtDuration(20000)).toBe('<1 min')
    expect(fmtDuration(125 * 60000)).toBe('2 h 5 min')
    expect(fmtTokens(1_200_000)).toBe('1.2M tokens')
    expect(fmtTokens(48_300)).toBe('48k tokens')
    expect(fmtTokens(512)).toBe('512 tokens')
  })
  it('builds the thread line', () => {
    const thread = {
      steps: 4,
      duration_ms: 41 * 60000,
      cost_usd: 2.1,
      tokens: 1_200_000,
      last: { title: 'Fix login', duration_ms: 18 * 60000, cost_usd: 1.4 },
    }
    expect(soFarLine(thread)).toBe('4 steps · 41 min · $2.10 · 1.2M tokens — last: Fix login, 18 min, $1.40')
  })
  it('one step, no last, no thread', () => {
    expect(soFarLine({ steps: 1, duration_ms: 60000, cost_usd: 0, tokens: 0, last: null })).toBe(
      '1 step · 1 min · $0.00 · 0 tokens',
    )
    expect(soFarLine(null)).toBeNull()
    expect(soFarLine({ steps: 0 })).toBeNull()
  })
  it('shows hidden characters in the title as markers', () => {
    const line = soFarLine({ steps: 1, duration_ms: 0, cost_usd: 0, tokens: 0, last: { title: 'a‮b', duration_ms: 0, cost_usd: 0 } })
    expect(line).toContain('a⟦U+202E⟧b')
  })
})

describe('usageLine (run-log lines)', () => {
  it('budget bands', () => {
    expect(usageLine({ type: 'usage_band', payload: { band: 'budget80', value: 0.8123 } })).toEqual({
      kind: 'budget80',
      text: 'budget about 80 % used ($0.81): the agent was told to save its notes',
    })
    expect(usageLine({ type: 'usage_band', payload: { band: 'budget95', value: 1.9 } })).toEqual({
      kind: 'budget95',
      text: 'budget about 95 % used ($1.90): the agent was told to finish up and end its turn',
    })
  })
  it('the fresh-session band', () => {
    expect(usageLine({ type: 'usage_band', payload: { band: 'fresh', value: 152400 } })).toEqual({
      kind: 'fresh',
      text: 'context reached 152k: the agent was told to save notes and end its turn; the thread continues in a fresh session',
    })
  })
  it('a band without a usable value still gets a line', () => {
    expect(usageLine({ type: 'usage_band', payload: { band: 'fresh', value: 'x' } }).text).toMatch(/^context reached the threshold/)
    expect(usageLine({ type: 'usage_band', payload: { band: 'budget80' } }).text).toMatch(/^budget about 80 % used:/)
    expect(usageLine({ type: 'usage_band', payload: { band: 'odd​' } })).toEqual({ kind: 'band', text: 'usage band odd⟦U+200B⟧' })
  })
  it('auto_continue uses the server text, else builds it', () => {
    expect(
      usageLine({ type: 'auto_continue', payload: { text: 'continued automatically: context reached 152k', context_tokens: 152400 } }),
    ).toEqual({ kind: 'auto', text: 'continued automatically: context reached 152k' })
    expect(usageLine({ type: 'auto_continue', payload: { context_tokens: 160000 } }).text).toBe(
      'continued automatically: context reached 160k',
    )
  })
  it('other events are not usage lines', () => {
    expect(usageLine({ type: 'guard', payload: {} })).toBeNull()
  })
})

describe('freshSessionError (project setting)', () => {
  it('has the bounds and the suggestion', () => {
    expect([FRESH_MIN, FRESH_MAX, FRESH_SUGGEST]).toEqual([50000, 900000, 150000])
  })
  it('accepts off and whole numbers in range', () => {
    expect(freshSessionError(null)).toBeNull()
    expect(freshSessionError(undefined)).toBeNull()
    expect(freshSessionError('')).toBeNull()
    expect(freshSessionError(50000)).toBeNull()
    expect(freshSessionError(900000)).toBeNull()
  })
  it('rejects values outside 50,000–900,000 and fractions', () => {
    expect(freshSessionError(49999)).toMatch(/50,000/)
    expect(freshSessionError(900001)).toMatch(/900,000/)
    expect(freshSessionError(150000.5)).toMatch(/whole/)
  })
})
