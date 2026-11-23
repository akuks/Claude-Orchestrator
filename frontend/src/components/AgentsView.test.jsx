import { act } from 'react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { flush, render } from '../home/testRender'

vi.mock('../api', () => ({
  api: {
    listAgents: vi.fn(),
    agentPresets: vi.fn(),
    guardOrgSettings: vi.fn(),
    createAgent: vi.fn(),
    updateAgent: vi.fn(),
    deleteAgent: vi.fn(),
  },
}))

import { api } from '../api'
import AgentsView from './AgentsView'

const click = (el) => act(async () => { el.dispatchEvent(new MouseEvent('click', { bubbles: true })) })
const btn = (label) => [...document.querySelectorAll('button')].find((b) => b.textContent.replace(/\s/g, '') === label.replace(/\s/g, ''))
async function type(el, value) {
  await act(async () => {
    const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement : HTMLInputElement
    Object.getOwnPropertyDescriptor(proto.prototype, 'value').set.call(el, value)
    el.dispatchEvent(new Event('input', { bubbles: true }))
  })
}
const modal = () => document.querySelector('.ant-modal')

let view
beforeAll(() => {
  window.matchMedia ||= () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} })
})
beforeEach(() => {
  vi.clearAllMocks()
  api.listAgents.mockImplementation(async () => [])
  api.agentPresets.mockImplementation(async () => [])
  api.guardOrgSettings.mockImplementation(async () => ({ model_tiers: { strongest: 'opus', standard: 'sonnet', light: 'haiku' } }))
  api.createAgent.mockImplementation(async (b) => ({ id: 'a1', ...b, lint: [] }))
})
afterEach(async () => {
  await view?.unmount()
  view = null
})

async function mount(props = {}) {
  view = await render(<AgentsView isAdmin projects={[]} {...props} />)
  await flush()
  await flush()
}
async function fill() {
  const inputs = modal().querySelectorAll('input.ant-input, textarea')
  await type(modal().querySelector('input[id$="name"]'), 'Sec')
  await type(modal().querySelector('textarea[id$="system_prompt"]'), 'You audit.')
}

describe('AgentsView tiers and lint', () => {
  it('the form shows a Tier select and its help', async () => {
    await mount()
    await click(btn('New Agent'))
    await flush()
    expect(modal().textContent).toContain('Tier')
    expect(modal().textContent).toContain('A model set here overrides the tier.')
    expect(modal().querySelector('[data-testid="agent-tier"]')).not.toBeNull()
  })

  it('saving with lint warnings shows them and keeps the modal open', async () => {
    api.createAgent.mockImplementation(async (b) => ({ id: 'a1', ...b, lint: ['the description should say why ("… because …")'] }))
    await mount()
    await click(btn('New Agent'))
    await flush()
    await fill()
    await click([...modal().querySelectorAll('button')].find((b) => b.textContent.trim() === 'OK'))
    await flush()
    expect(api.createAgent).toHaveBeenCalled()
    expect(modal().textContent).toContain('Saved with warnings')
    expect(modal().textContent).toContain('the description should say why')
  })

  it('saving without lint closes the modal', async () => {
    await mount()
    await click(btn('New Agent'))
    await flush()
    await fill()
    await click([...modal().querySelectorAll('button')].find((b) => b.textContent.trim() === 'OK'))
    await flush()
    await flush()
    expect(api.createAgent).toHaveBeenCalled()
    expect(document.body.textContent).not.toContain('Saved with warnings')
    expect(modal()?.closest('.ant-modal-wrap')?.style.display || 'none').toBe('none')
  })

  it('a preset with tier light pre-selects Light and seeds the tier', async () => {
    api.agentPresets.mockImplementation(async () => [
      { name: 'Checker', description: 'd', system_prompt: 'You check.', tier: 'light', read_only: true, category: 'Helpers' },
    ])
    await mount()
    await click(btn('Checker'))
    await flush()
    expect(modal().querySelector('[data-testid="agent-tier"]').closest('.ant-select').textContent).toContain('Light')
    await click([...modal().querySelectorAll('button')].find((b) => b.textContent.trim() === 'OK'))
    await flush()
    expect(api.createAgent.mock.calls[0][0]).toMatchObject({ tier: 'light', read_only: true })
  })

  it('a list row with lint shows a warning icon whose title has the text', async () => {
    api.listAgents.mockImplementation(async () => [
      { id: 'a1', name: 'Old', tier: 'standard', lint: ['the description should say why ("… because …")'], folder: '' },
    ])
    await mount()
    const w = document.querySelector('[data-testid="agent-lint-a1"]')
    expect(w).not.toBeNull()
    expect(w.getAttribute('aria-label')).toContain('the description should say why')
    expect(document.body.textContent).toContain('standard')
  })
})
