import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from '../api'

describe('api.systemHealth', () => {
  afterEach(() => vi.unstubAllGlobals())
  it('asks the server to skip its cache only when refreshing', async () => {
    const f = vi.fn(async () => ({ ok: true, status: 200, json: async () => ({ checks: [] }) }))
    vi.stubGlobal('fetch', f)
    await api.systemHealth(false)
    await api.systemHealth(true)
    expect(f.mock.calls[0][0]).toBe('/admin/health')
    expect(f.mock.calls[1][0]).toBe('/admin/health?refresh=true')
  })
})
