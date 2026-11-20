import { describe, expect, it } from 'vitest'
import { timelineComet } from './timeline'

const st = (name, state, task_count = 1) => ({ name, state, task_count })

describe('timelineComet', () => {
  it('maps stages to stars left to right, keeping name, state and task count', () => {
    const c = timelineComet([st('Plan', 'done', 2), st('Build', 'current', 3), st('Ship', 'next', 0)])
    expect(c.stars.map((s) => [s.name, s.state, s.taskCount])).toEqual([
      ['Plan', 'done', 2],
      ['Build', 'current', 3],
      ['Ship', 'next', 0],
    ])
    const xs = c.stars.map((s) => s.x)
    expect(xs[0]).toBeLessThan(xs[1])
    expect(xs[1]).toBeLessThan(xs[2])
    expect(xs[0]).toBeGreaterThanOrEqual(0)
    expect(xs[2]).toBeLessThanOrEqual(1)
    for (const s of c.stars) {
      expect(s.y).toBeGreaterThanOrEqual(0)
      expect(s.y).toBeLessThanOrEqual(1)
      expect(s.z).toBeGreaterThanOrEqual(-1)
      expect(s.z).toBeLessThanOrEqual(1)
    }
  })

  it('puts the head on the in-progress, approval-waiting or failed stage', () => {
    expect(timelineComet([st('A', 'done'), st('B', 'current'), st('C', 'next')]).headIndex).toBe(1)
    expect(timelineComet([st('A', 'done'), st('B', 'done'), st('C', 'waiting_approval')]).headIndex).toBe(2)
    expect(timelineComet([st('A', 'failed'), st('B', 'next')]).headIndex).toBe(0)
    // all done: the head rests on the last done stage
    expect(timelineComet([st('A', 'done'), st('B', 'done')]).headIndex).toBe(1)
  })

  it('treats unknown or missing states as "next" and missing counts as 0', () => {
    const c = timelineComet([{ name: 'X', state: 'weird' }, { name: 'Y' }])
    expect(c.stars.map((s) => s.state)).toEqual(['next', 'next'])
    expect(c.stars.map((s) => s.taskCount)).toEqual([0, 0])
  })

  it('centres a single stage and returns no stars for no stages', () => {
    expect(timelineComet([st('Only', 'current')]).stars[0].x).toBeCloseTo(0.5)
    expect(timelineComet([])).toEqual({ stars: [], headIndex: -1 })
    expect(timelineComet(undefined)).toEqual({ stars: [], headIndex: -1 })
  })

  it('is deterministic', () => {
    const s = [st('A', 'done'), st('B', 'current'), st('C', 'next'), st('D', 'next')]
    expect(timelineComet(s)).toEqual(timelineComet(s))
  })
})
