import { describe, expect, it } from 'vitest'
import { antdTheme, cssVars, modeTokens, palette, statusColor, statusVar } from './tokens'

// Relative luminance of a #rrggbb colour (0 = black, 1 = white).
function luminance(hex) {
  const n = parseInt(hex.slice(1), 16)
  const ch = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map((c) => {
    const s = c / 255
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4
  })
  return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2]
}

describe('palette', () => {
  it('carries the Sunset tokens from the spec', () => {
    expect(palette.primary).toBe('#f97316')
    expect(palette.gradient).toBe('linear-gradient(135deg,#e11d48,#f97316)')
    expect(palette.status).toEqual({
      success: '#10b981',
      warning: '#f59e0b',
      danger: '#e11d48',
      info: '#6366f1',
    })
    expect(palette.light.bg).toBe('linear-gradient(180deg,#ffffff,#fff7f0 60%,#fff4f6)')
    expect(palette.light.surface).toBe('rgba(255,255,255,.85)')
    expect(palette.light.border).toBe('#fde7d6')
    expect(palette.light.text).toBe('#3b2433')
    expect(palette.light.textSecondary).toBe('#8a6f7d')
    expect(palette.night.surface).toBe('rgba(255,255,255,.06)')
    expect(palette.night.text).toBe('#fbeff3')
    expect(palette.night.bg).toBe('linear-gradient(180deg,#0f0a14,#1a0f1f)')
  })
})

describe('antdTheme', () => {
  it('light uses the sunset primary', () => {
    expect(antdTheme('light').token.colorPrimary).toBe('#f97316')
  })

  it('night has a dark base background and light text', () => {
    const t = antdTheme('night').token
    expect(luminance(t.colorBgBase)).toBeLessThan(0.05)
    expect(t.colorTextBase).toBe('#fbeff3')
    expect(t.colorPrimary).toBe('#f97316')
  })

  it('light has a light base background', () => {
    expect(luminance(antdTheme('light').token.colorBgBase)).toBeGreaterThan(0.8)
  })

  it('uses Inter and status tokens in both modes', () => {
    for (const mode of ['light', 'night']) {
      const t = antdTheme(mode).token
      expect(t.fontFamily).toMatch(/^Inter/)
      expect(t.colorSuccess).toBe('#10b981')
      expect(t.colorWarning).toBe('#f59e0b')
      expect(t.colorError).toBe('#e11d48')
    }
  })
})

describe('cssVars', () => {
  it('returns the same keys for both modes', () => {
    const light = cssVars('light')
    const night = cssVars('night')
    expect(Object.keys(light).sort()).toEqual(Object.keys(night).sort())
    expect(Object.keys(light).length).toBeGreaterThan(10)
    for (const k of Object.keys(light)) expect(k.startsWith('--co-')).toBe(true)
  })

  it('switches background and text between modes', () => {
    expect(cssVars('light')['--co-bg']).toBe(palette.light.bg)
    expect(cssVars('night')['--co-bg']).toBe(palette.night.bg)
    expect(cssVars('night')['--co-text']).toBe('#fbeff3')
  })

  it('keeps the terminal dark in both modes', () => {
    expect(cssVars('light')['--co-terminal-bg']).toBe(cssVars('night')['--co-terminal-bg'])
    expect(luminance(cssVars('light')['--co-terminal-bg'])).toBeLessThan(0.05)
  })
})

describe('modeTokens / statusColor', () => {
  it('resolves concrete colours per mode for canvas charts', () => {
    expect(modeTokens('light').text).toBe('#3b2433')
    expect(modeTokens('night').text).toBe('#fbeff3')
    expect(modeTokens('night').textSecondary).not.toBe(modeTokens('light').textSecondary)
  })

  it('maps task statuses onto status tokens', () => {
    expect(statusColor('completed', 'light')).toBe('#10b981')
    expect(statusColor('failed', 'night')).toBe('#e11d48')
    expect(statusColor('running')).toBe('#6366f1')
    expect(statusColor('awaiting_approval')).toBe('#f59e0b')
    expect(statusColor('queued', 'light')).toBe(modeTokens('light').textMuted)
    expect(statusColor('nonsense', 'night')).toBe(modeTokens('night').textMuted)
  })
})

describe('statusVar', () => {
  it('maps statuses onto CSS variables that exist in cssVars', () => {
    const keys = Object.keys(cssVars('light'))
    for (const s of ['completed', 'failed', 'running', 'awaiting_approval', 'queued', 'cancelled', 'x']) {
      const v = statusVar(s)
      const name = v.match(/^var\((--co-[a-z-]+)\)$/)[1]
      expect(keys).toContain(name)
    }
    expect(statusVar('completed')).toBe('var(--co-success)')
    expect(statusVar('queued')).toBe('var(--co-text-muted)')
  })
})
