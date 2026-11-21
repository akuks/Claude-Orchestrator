import { act } from 'react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { flush, render } from '../home/testRender'

vi.mock('../api', () => ({ api: { createServer: vi.fn(async (b) => b), updateServer: vi.fn(async (id, b) => b) } }))

import { api } from '../api'
import McpServerModal from './McpServerModal'

const click = (el) => act(async () => { el.dispatchEvent(new MouseEvent('click', { bubbles: true })) })
const btn = (label) => [...document.querySelectorAll('.ant-modal button')].find((b) => b.textContent.trim() === label)
async function type(el, value) {
  await act(async () => {
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set
    setter.call(el, value)
    el.dispatchEvent(new Event('input', { bubbles: true }))
  })
}
async function pick(testid, label) {
  const sel = document.querySelector(`[data-testid="${testid}"]`).closest('.ant-select')
  await act(async () => {
    sel.querySelector('.ant-select-selector').dispatchEvent(new MouseEvent('mousedown', { bubbles: true }))
  })
  await flush()
  const opt = [...document.querySelectorAll('.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option')].find(
    (o) => o.textContent === label,
  )
  await click(opt)
}

let view
beforeAll(() => {
  window.matchMedia ||= () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} })
})
beforeEach(() => vi.clearAllMocks())
afterEach(async () => {
  await view?.unmount()
  view = null
  document.body.innerHTML = ''
})

const server = {
  id: 's1', name: 'pg', transport: 'stdio', scope: 'team', project: null, command: 'npx', args: ['-y', 'pg-mcp'],
  url: null, enabled: true, environment: 'unknown', default_action: 'auto_approve',
}

describe('McpServerModal: co-guard fields', () => {
  it('a new server defaults to unknown / require approval and sends both', async () => {
    view = await render(<McpServerModal open server={null} onClose={() => {}} />)
    await flush()
    expect(document.body.textContent).toContain('Unknown (treated as production)')
    expect(document.body.textContent).toContain('Require approval')
    await type(document.querySelector('input[placeholder="e.g. github"]'), 'db')
    await type(document.querySelector('input[placeholder="e.g. npx"]'), 'npx')
    await pick('mcp-environment', 'Dev')
    await click(btn('Add'))
    await flush()
    expect(api.createServer).toHaveBeenCalledWith(
      expect.objectContaining({ name: 'db', environment: 'dev', default_action: 'require_approval' }),
    )
  })

  it("editing shows the server's values and sends the changed default action", async () => {
    view = await render(<McpServerModal open server={server} onClose={() => {}} />)
    await flush()
    expect(document.body.textContent).toContain('Auto-approve')
    await pick('mcp-default-action', 'Block')
    await pick('mcp-environment', 'Production')
    await click(btn('Save'))
    await flush()
    expect(api.updateServer).toHaveBeenCalledWith('s1', expect.objectContaining({ environment: 'prod', default_action: 'block' }))
  })
})
