// Every REST path the client calls must be proxied to the backend by the Vite
// dev server (start.sh serves the UI with `vite`): an unproxied prefix gets
// index.html back, so the call fails silently. Found by the phase 3b e2e
// (/questions and /handoffs were missing: no Home Questions, Open handoffs or
// Rail badge).
import { describe, expect, it } from 'vitest'
import fs from 'node:fs'
import path from 'node:path'

const api = fs.readFileSync(path.resolve(__dirname, 'api.js'), 'utf8')
// read as text: importing the config pulls in vite's plugins, which do not load under jsdom
const viteConfig = fs.readFileSync(path.resolve(__dirname, '../vite.config.js'), 'utf8')
const proxyKeys = () => [...viteConfig.matchAll(/'(\/[^']*)':\s*\{\s*target/g)].map((m) => m[1])

function calledPaths() {
  const out = new Set()
  for (const m of api.matchAll(/req\(\s*'[A-Z]+',\s*[`']([^`'$?]+)/g)) out.add(m[1])
  for (const m of api.matchAll(/[`'](\/[a-z][a-z-]*\/?)/g)) out.add(m[1])
  return [...out].filter((p) => p.startsWith('/'))
}

describe('vite dev proxy', () => {
  it('forwards every API path prefix the client uses', () => {
    const keys = proxyKeys()
    expect(keys).toContain('/tasks')
    const paths = calledPaths()
    expect(paths).toContain('/questions')
    expect(paths).toContain('/handoffs/open')
    const missing = paths.filter((p) => !keys.some((k) => p === k || p.startsWith(k.endsWith('/') ? k : k + '/')))
    expect(missing).toEqual([])
  })
})
