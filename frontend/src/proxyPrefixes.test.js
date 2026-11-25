// Every top-level prefix a backend router serves must be proxied by the Vite dev server,
// or the call gets index.html back and fails silently (found for /plan in phase 7c).
import { describe, expect, it } from 'vitest'
import fs from 'node:fs'
import path from 'node:path'

const routers = path.resolve(__dirname, '../../backend/app/routers')
const viteConfig = fs.readFileSync(path.resolve(__dirname, '../vite.config.js'), 'utf8')
const keys = [...viteConfig.matchAll(/'(\/[^']*)':\s*\{\s*target/g)].map((m) => m[1])
// GitHub calls webhooks on the backend directly, not through the UI.
const NOT_FOR_THE_UI = new Set(['/webhooks'])

describe('vite dev proxy covers the backend routers', () => {
  it('has an entry for every router prefix', () => {
    const prefixes = new Set()
    for (const f of fs.readdirSync(routers).filter((n) => n.endsWith('.py'))) {
      const src = fs.readFileSync(path.join(routers, f), 'utf8')
      for (const m of src.matchAll(/APIRouter\([^)]*prefix="(\/[^"/]*)/g)) prefixes.add(m[1])
    }
    expect(prefixes.size).toBeGreaterThan(10)
    const missing = [...prefixes].filter((p) => !NOT_FOR_THE_UI.has(p) && !keys.some((k) => k === p || k === `${p}/`))
    expect(missing).toEqual([])
  })
})
