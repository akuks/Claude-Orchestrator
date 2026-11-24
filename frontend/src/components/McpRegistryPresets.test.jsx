import { act } from 'react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { flush, render } from '../home/testRender'

vi.mock('../api', () => ({
  MCP_STATUS_COLORS: {},
  api: {
    listServers: vi.fn(async () => []), mcpObservability: vi.fn(async () => ({ window_days: 7, total_calls: 0, failure_rate: 0, top_tools: [] })),
    listMcpPresets: vi.fn(async () => [
      { key: 'code-map', name: 'Code map', description: 'Builds a map.', transport: 'stdio', command: 'npx', args: ['-y', 'codebase-memory-mcp@0.12.1'], pinned: '0.12.1' },
    ]),
    createServer: vi.fn(),
  },
}))
import { api } from '../api'
import McpRegistry from './McpRegistry'

let view
beforeAll(() => {
  window.matchMedia ||= () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} })
})
afterEach(async () => {
  await view?.unmount()
  document.body.innerHTML = ''
})

describe('presets', () => {
  it('admins see cards; Add pre-fills the form without saving', async () => {
    view = await render(<McpRegistry isAdmin />)
    await flush()
    const add = document.querySelector('[data-testid="preset-add-code-map"]')
    expect(add).not.toBeNull()
    await act(async () => add.dispatchEvent(new MouseEvent('click', { bubbles: true })))
    await flush()
    expect(document.querySelector('input[placeholder="e.g. github"]').value).toBe('Code map')
    expect(document.querySelector('input[placeholder="e.g. npx"]').value).toBe('npx')
    expect(document.body.textContent).toContain('-y codebase-memory-mcp@0.12.1')
    expect(api.createServer).not.toHaveBeenCalled()
  })
  it('non-admins do not see presets', async () => {
    view = await render(<McpRegistry />)
    await flush()
    expect(document.querySelector('[data-testid="preset-row"]')).toBeNull()
  })
})
