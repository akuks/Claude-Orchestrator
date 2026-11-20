import { describe, expect, it } from 'vitest'
import { headIndexOf, layoutComets } from './layout'

const item = (i, states = ['done', 'current', 'next'], extra = {}) => ({
  kind: 'run',
  id: `run-${i}`,
  title: `Run ${i}`,
  project_ids: [],
  status: 'running',
  created_at: '2026-11-20T10:00:00Z',
  stages: states.map((state, j) => ({ name: `S${j}`, state, task_count: 1 })),
  ...extra,
})

describe('layoutComets', () => {
  it('caps at 12 comets and reports the overflow', () => {
    const items = Array.from({ length: 30 }, (_, i) => item(i))
    const { comets, overflow } = layoutComets(items)
    expect(comets).toHaveLength(12)
    expect(overflow).toBe(18)
    expect(comets.map((c) => c.id)).toEqual(items.slice(0, 12).map((i) => i.id))
  })

  it('honours a custom max and no overflow under it', () => {
    expect(layoutComets([item(1), item(2)], { max: 1 }).overflow).toBe(1)
    expect(layoutComets([item(1), item(2)]).overflow).toBe(0)
    expect(layoutComets([])).toEqual({ comets: [], overflow: 0 })
    expect(layoutComets(undefined)).toEqual({ comets: [], overflow: 0 })
  })

  it('is deterministic: same input gives the same coordinates', () => {
    const items = Array.from({ length: 5 }, (_, i) => item(i, ['done', 'done', 'current', 'next', 'next']))
    const a = layoutComets(items)
    const b = layoutComets(JSON.parse(JSON.stringify(items)))
    expect(a).toEqual(b)
  })

  it('places stars in the unit box, left to right, with the stage names and states', () => {
    const { comets } = layoutComets([item(1, ['done', 'failed', 'next', 'waiting_approval'])])
    const [c] = comets
    expect(c).toMatchObject({ id: 'run-1', kind: 'run', title: 'Run 1' })
    expect(c.stars.map((s) => [s.name, s.state])).toEqual([
      ['S0', 'done'],
      ['S1', 'failed'],
      ['S2', 'next'],
      ['S3', 'waiting_approval'],
    ])
    for (const s of c.stars) {
      expect(s.x).toBeGreaterThanOrEqual(0)
      expect(s.x).toBeLessThanOrEqual(1)
      expect(s.y).toBeGreaterThanOrEqual(0)
      expect(s.y).toBeLessThanOrEqual(1)
      expect(s.z).toBeGreaterThanOrEqual(-1)
      expect(s.z).toBeLessThanOrEqual(1)
    }
    const xs = c.stars.map((s) => s.x)
    expect([...xs].sort((p, q) => p - q)).toEqual(xs)
    expect(new Set(xs).size).toBe(xs.length)
  })

  it('gives different comets different paths', () => {
    const { comets } = layoutComets([item(1), item(2), item(3)])
    const ys = comets.map((c) => c.stars[1].y)
    expect(new Set(ys).size).toBe(3)
  })

  it('a comet without stages still gets one star', () => {
    const { comets } = layoutComets([item(1, [], { kind: 'thread' })])
    expect(comets[0].stars).toHaveLength(1)
    expect(comets[0].stars[0].state).toBe('current')
    expect(comets[0].headIndex).toBe(0)
  })
})

describe('headIndex', () => {
  const stars = (...states) => states.map((state) => ({ state }))
  it('is the current star', () => {
    expect(headIndexOf(stars('done', 'done', 'current', 'next'))).toBe(2)
    expect(layoutComets([item(1, ['done', 'current', 'next'])]).comets[0].headIndex).toBe(1)
  })
  it('a failed or approval-waiting stage is where the run stands', () => {
    expect(headIndexOf(stars('done', 'failed', 'next'))).toBe(1)
    expect(headIndexOf(stars('done', 'waiting_approval', 'next'))).toBe(1)
  })
  it('falls back to the last done star, then the first', () => {
    expect(headIndexOf(stars('done', 'done', 'next'))).toBe(1)
    expect(headIndexOf(stars('next', 'next'))).toBe(0)
  })
})
