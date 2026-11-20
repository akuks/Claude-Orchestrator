// @vitest-environment node
import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

describe('comets.css', () => {
  it('has no hard-coded colours (all come from theme variables)', () => {
    const css = readFileSync(new URL('./comets.css', import.meta.url), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '')
    expect(css.match(/#[0-9a-fA-F]{3,8}\b|rgba?\(/g)).toBeNull()
  })
})
