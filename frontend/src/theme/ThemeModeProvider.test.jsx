import { act } from 'react'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { flush, render } from '../home/testRender'
import { ThemeModeProvider, useThemeMode } from './ThemeModeContext.jsx'
import TopBar from '../shell/TopBar.jsx'

// A controllable prefers-reduced-motion media query.
function fakeMedia(initial) {
  const listeners = new Set()
  const mq = {
    matches: initial,
    media: '(prefers-reduced-motion: reduce)',
    addEventListener: (_t, fn) => listeners.add(fn),
    removeEventListener: (_t, fn) => listeners.delete(fn),
    addListener: (fn) => listeners.add(fn),
    removeListener: (fn) => listeners.delete(fn),
  }
  return {
    matchMedia: (q) => (q.includes('prefers-reduced-motion') ? mq : { matches: false, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} }),
    set(v) {
      mq.matches = v
      for (const fn of [...listeners]) fn({ matches: v, media: mq.media })
    },
    listeners,
  }
}

let seen
function Probe() {
  seen = useThemeMode()
  return null
}

describe('ThemeModeProvider reduce motion', () => {
  const realMM = window.matchMedia
  let media, view
  beforeEach(() => {
    localStorage.clear()
    media = fakeMedia(false)
    window.matchMedia = media.matchMedia
  })
  afterEach(async () => {
    await view?.unmount()
    view = null
    window.matchMedia = realMM
    localStorage.clear()
  })

  it('system (default) follows prefers-reduced-motion live', async () => {
    view = await render(<ThemeModeProvider><Probe /></ThemeModeProvider>)
    expect(seen.motionPref).toBe('system')
    expect(seen.reduceMotion).toBe(false)
    await act(async () => media.set(true))
    expect(seen.reduceMotion).toBe(true)
    expect(document.documentElement.dataset.reduceMotion).toBe('true')
    await act(async () => media.set(false))
    expect(seen.reduceMotion).toBe(false)
  })

  it('on / off override the OS and are remembered', async () => {
    view = await render(<ThemeModeProvider><Probe /></ThemeModeProvider>)
    await act(async () => seen.setMotionPref('on'))
    expect(seen.reduceMotion).toBe(true)
    expect(localStorage.getItem('co.reduceMotion')).toBe('on')
    await act(async () => seen.setMotionPref('off'))
    await act(async () => media.set(true))
    expect(seen.reduceMotion).toBe(false)
    expect(localStorage.getItem('co.reduceMotion')).toBe('off')
    await act(async () => seen.setMotionPref('system'))
    expect(seen.reduceMotion).toBe(true)
  })

  it('stops listening on unmount', async () => {
    view = await render(<ThemeModeProvider><Probe /></ThemeModeProvider>)
    expect(media.listeners.size).toBe(1)
    await view.unmount()
    view = null
    expect(media.listeners.size).toBe(0)
  })

  it('the avatar menu offers System / On / Off', async () => {
    const user = { id: 'u', name: 'Ada Lovelace', email: 'a@x', role: 'member' }
    view = await render(
      <ThemeModeProvider>
        <Probe />
        <TopBar user={user} onOpenPalette={() => {}} />
      </ThemeModeProvider>,
    )
    await act(async () => view.container.querySelector('[data-testid="user-menu"]').click())
    await flush()
    const opt = (v) => document.body.querySelector(`[data-testid="motion-${v}"]`)
    for (const v of ['system', 'on', 'off']) expect(opt(v), v).toBeTruthy()
    expect(opt('system').getAttribute('data-checked')).toBe('true')
    expect(opt('on').getAttribute('data-checked')).toBe('false')
    await act(async () => opt('on').click())
    expect(seen.motionPref).toBe('on')
    expect(localStorage.getItem('co.reduceMotion')).toBe('on')
  })
})
