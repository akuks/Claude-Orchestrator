import { describe, expect, it } from 'vitest'
import { applyThemeToDocument, initialMode, initialReduceMotion, persist } from './ThemeModeContext.jsx'
import { cssVars } from './tokens'

const store = (init = {}) => {
  const data = { ...init }
  return {
    data,
    getItem: (k) => (k in data ? data[k] : null),
    setItem: (k, v) => {
      data[k] = String(v)
    },
  }
}
const throwing = {
  getItem: () => {
    throw new Error('blocked')
  },
  setItem: () => {
    throw new Error('blocked')
  },
}
const mm = (matches) => () => ({ matches })

describe('initialMode', () => {
  it('defaults to light and reads co.mode', () => {
    expect(initialMode(store())).toBe('light')
    expect(initialMode(store({ 'co.mode': 'night' }))).toBe('night')
    expect(initialMode(store({ 'co.mode': 'bogus' }))).toBe('light')
  })
  it('survives blocked storage', () => {
    expect(initialMode(throwing)).toBe('light')
    expect(initialMode(undefined)).toBe('light')
  })
})

describe('initialReduceMotion', () => {
  it('uses the stored setting when present', () => {
    expect(initialReduceMotion(store({ 'co.reduceMotion': 'true' }), mm(false))).toBe(true)
    expect(initialReduceMotion(store({ 'co.reduceMotion': 'false' }), mm(true))).toBe(false)
  })
  it('falls back to prefers-reduced-motion', () => {
    expect(initialReduceMotion(store(), mm(true))).toBe(true)
    expect(initialReduceMotion(throwing, mm(true))).toBe(true)
    expect(initialReduceMotion(store(), undefined)).toBe(false)
  })
})

describe('persist / applyThemeToDocument', () => {
  it('writes values and swallows storage errors', () => {
    const s = store()
    persist(s, 'co.mode', 'night')
    expect(s.data['co.mode']).toBe('night')
    expect(() => persist(throwing, 'co.mode', 'night')).not.toThrow()
  })
  it('sets CSS variables and data attributes on the root element', () => {
    const el = document.createElement('div')
    applyThemeToDocument(el, 'night', true)
    expect(el.dataset.mode).toBe('night')
    expect(el.dataset.reduceMotion).toBe('true')
    expect(el.style.getPropertyValue('--co-text')).toBe(cssVars('night')['--co-text'])
  })
})
