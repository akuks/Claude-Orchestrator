// @vitest-environment node
import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

const css = readFileSync(new URL('./projects.css', import.meta.url), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '')
const src = readFileSync(new URL('./ProjectCards.jsx', import.meta.url), 'utf8')

describe('project cards styling', () => {
  it('has no hard-coded colours and no antd preset tag colours (tokens only)', () => {
    expect(css.match(/#[0-9a-fA-F]{3,8}\b|rgba?\(/g)).toBeNull()
    expect(src).not.toMatch(/<Tag[^>]*\scolor=/)
    expect(css).toMatch(/\.co-pcard-tag-live\s*\{[^}]*var\(--co-/)
    expect(css).toMatch(/\.co-pcard-tag-paused\s*\{[^}]*var\(--co-/)
  })
  it('dims archived cards without lowering text contrast (no opacity on the card)', () => {
    const rule = css.match(/\.co-pcard-archived\s*\{([^}]*)\}/)
    expect(rule).not.toBeNull()
    expect(rule[1]).not.toMatch(/opacity/)
  })
})
