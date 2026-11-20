import { describe, expect, it } from 'vitest'
import { when } from './time'

describe('when (dayjs for backend timestamps)', () => {
  it('reads offset-less timestamps (SQLite) as UTC, like ones with an offset', () => {
    expect(when('2026-11-20T11:00:00').valueOf()).toBe(Date.UTC(2026, 10, 20, 11))
    expect(when('2026-11-20T11:00:00.5').valueOf()).toBe(Date.UTC(2026, 10, 20, 11, 0, 0, 500))
    expect(when('2026-11-20T11:00:00+00:00').valueOf()).toBe(Date.UTC(2026, 10, 20, 11))
  })
  it('passes other values through dayjs', () => {
    expect(when(Date.UTC(2026, 0, 1)).valueOf()).toBe(Date.UTC(2026, 0, 1))
    expect(when('2026-01-02').isValid()).toBe(true)
    expect(when(null).isValid()).toBe(false)
  })
})
