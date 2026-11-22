import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'

// Colours come from tokens (CSS variables / antd tokens), never literals.
describe('guard, resume, usage and questions UI use tokens', () => {
  for (const d of ['src/guard', 'src/resume', 'src/usage', 'src/questions']) {
    const dir = resolve(process.cwd(), d)
    const files = readdirSync(dir).filter((n) => /\.(jsx|css)$/.test(n) && !n.includes('.test.'))
    it(`${d} has .jsx and .css files to check`, () => {
      expect(files.some((n) => n.endsWith('.jsx'))).toBe(true)
      expect(files.some((n) => n.endsWith('.css'))).toBe(true)
    })
    for (const f of files)
      it(`${d}/${f} has no hard-coded hex colours`, () => {
        expect(readFileSync(resolve(dir, f), 'utf8')).not.toMatch(/#[0-9a-fA-F]{3,8}\b/)
      })
  }
})
