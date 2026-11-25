import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flush, render } from '../home/testRender'
import dayjs from 'dayjs'
import { bannerText, eventLine, pausedText, localWhen, localizeServerText, userTag } from './plan'

vi.mock('../api', () => ({ api: { planStatus: vi.fn(), planStatusAll: vi.fn() } }))
import { api } from '../api'
import PlanBanner from './PlanBanner'
import PlanCard from './PlanCard'

const now = dayjs('2026-11-25T10:00:00')
const at = (s) => dayjs(s).toISOString()

describe('plan texts', () => {
  it('local time, date only when not today', () => {
    expect(localWhen(at('2026-11-25T14:05:00'), now)).toBe('14:05')
    expect(localWhen(at('2026-11-26T09:10:00'), now)).toBe('tomorrow 09:10')
    expect(localWhen(at('2026-11-28T09:10:00'), now)).toBe('28 Nov, 09:10')
  })
  it('banner sentences', () => {
    const r = at('2026-11-25T14:04:00')
    expect(bannerText({ kind: 'limit', text: 'x', resets_at: r }, now)).toBe('Your Claude plan limit was reached. Work starts again at 14:05.')
    expect(bannerText({ kind: 'warning', text: 'x', resets_at: at('2026-11-25T14:05:00') }, now)).toBe('Your Claude plan is close to its limit. It resets at 14:05.')
    expect(bannerText({ kind: 'limit', text: 'server text', resets_at: null }, now)).toBe('server text')
  })
  it('paused task and event lines', () => {
    const t = at('2026-11-25T14:05:00')
    expect(pausedText({ not_before: t }, now)).toBe('Paused — your Claude plan limit was reached. Starts again at 14:05.')
    expect(pausedText({ not_before: at('2026-11-25T09:00:00') }, now)).toBeNull()
    expect(eventLine({ paused: true, not_before: t }, now)).toContain('Starts again at 14:05')
    expect(eventLine({ status: 'allowed_warning', resets_at: t }, now)).not.toMatch(/[{}]/)
  })
  it('localizes a server UTC time', () => {
    const out = localizeServerText('Paused — your Claude plan limit was reached. Starts again at 23:30.', now)
    expect(out).toMatch(/Starts again at (tomorrow |\d+ \w+, )?\d{2}:\d{2}\.$/)
  })
  it('user tags', () => {
    expect(userTag({ held: true, resets_at: at('2026-11-25T14:04:00') }, now).text).toBe('Limit reached until 14:05')
    expect(userTag({ held: false, banner: { kind: 'warning' } }).text).toBe('Close to limit')
    expect(userTag({ held: false, banner: null })).toBeNull()
  })
})

describe('plan screens', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    window.matchMedia ||= () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} })
  })
  it('banner only when set', async () => {
    api.planStatus.mockResolvedValue({ status: { banner: null } })
    const v = await render(<PlanBanner />)
    await flush()
    expect(v.container.querySelector('[data-testid="plan-banner"]')).toBeNull()
    api.planStatus.mockResolvedValue({ status: { banner: { kind: 'warning', text: 's', resets_at: new Date(Date.now() + 3600e3).toISOString() } } })
    await v.rerender(<PlanBanner refreshKey={1} />)
    await flush()
    expect(v.container.querySelector('[data-testid="plan-banner"]').textContent).toContain('close to its limit')
    await v.unmount()
  })
  const card = async (resp) => {
    api.planStatus.mockResolvedValue(resp)
    const v = await render(<PlanCard />)
    await flush()
    return v
  }
  it('card: api key, nothing yet, limit with details collapsed', async () => {
    let v = await card({ plan_applies: false, api_key: true, status: null })
    expect(v.container.textContent).toContain("Plan limits don't apply to API keys.")
    await v.unmount()
    v = await card({ plan_applies: true, api_key: false, status: null })
    expect(v.container.textContent).toContain('Nothing reported yet. It appears after the next task runs.')
    await v.unmount()
    v = await card({ plan_applies: true, api_key: false, status: { status: 'rejected', held: true, resets_at: new Date(Date.now() + 3600e3).toISOString(), seen_at: new Date().toISOString(), utilization: 0.97, raw: { zzz: 1 } } })
    expect(v.container.textContent).toContain('Limit reached')
    expect(v.container.textContent).toContain('Last checked')
    expect(v.container.textContent).not.toContain('zzz')
    await v.unmount()
  })
})
