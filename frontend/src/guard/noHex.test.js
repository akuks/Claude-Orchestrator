import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'

// Colours come from tokens (CSS variables / antd tokens), never literals.
describe('guard UI uses tokens', () => {
  const dir = resolve(process.cwd(), 'src/guard')
  for (const f of readdirSync(dir).filter((n) => n.endsWith('.jsx') && !n.includes('.test.')))
    it(`${f} has no hard-coded hex colours`, () => {
      expect(readFileSync(resolve(dir, f), 'utf8')).not.toMatch(/#[0-9a-fA-F]{3,8}\b/)
    })
})
