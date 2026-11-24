import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { flush, render } from '../home/testRender'

vi.mock('../api', () => ({
  api: {
    listSkills: vi.fn(),
    skillPresets: vi.fn(async () => []),
    checkSkillUpdate: vi.fn(),
    updateSkillPack: vi.fn(),
    lintSkillText: vi.fn(),
    skillTemplate: vi.fn(),
  },
}))

import { api } from '../api'
import { message } from 'antd'
import SkillsView from './SkillsView'

const click = async (el) => { await act(async () => { el.dispatchEvent(new MouseEvent('click', { bubbles: true })) }); await flush() }
const q = (id) => document.querySelector(`[data-testid="${id}"]`)
const pack = (o = {}) => ({
  id: 'p1', name: 'demo', source_url: 'https://github.com/a/b', scope: 'global', enabled: true,
  current_sha: 'abcdef1234567890', latest_sha: null, update_available: false, ...o,
})

beforeAll(() => {
  vi.spyOn(message, 'success').mockImplementation(() => {})
  window.matchMedia ||= () => ({ matches: true, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} })
})
afterEach(() => { document.body.innerHTML = ''; vi.clearAllMocks() })

describe('SkillsView updates', () => {
  it('keeps the version id under Details and checks for an update', async () => {
    api.listSkills.mockResolvedValue([pack()])
    api.checkSkillUpdate.mockResolvedValue({ update_available: false })
    await render(<SkillsView isAdmin />)
    expect(q('sha-p1').textContent).toBe('Version id: abcdef1')
    expect(q('details-p1').open).toBe(false)
    expect(q('details-p1').textContent).toContain('Details')
    expect(q('update-pack-p1')).toBeNull()
    await click(q('check-update-p1'))
    expect(api.checkSkillUpdate).toHaveBeenCalledWith('p1')
  })

  it('confirms before updating', async () => {
    api.listSkills.mockResolvedValue([pack({ update_available: true, latest_sha: '1234567abcdef' })])
    api.updateSkillPack.mockResolvedValue({ from: 'a', to: '1234567abcdef', lint: [] })
    await render(<SkillsView isAdmin />)
    expect(q('update-pack-p1').textContent).toBe('Update')
    expect(document.body.textContent).toContain('New version available')
    expect(document.body.textContent).not.toMatch(/commit|sha\b|fast-forward/i)
    await click(q('update-pack-p1'))
    expect(document.body.textContent).toContain('This pulls the newest version of this skill pack.')
    expect(api.updateSkillPack).not.toHaveBeenCalled()
    await click(document.querySelector('.ant-popover .ant-btn-primary'))
    expect(api.updateSkillPack).toHaveBeenCalledWith('p1')
    expect(message.success).toHaveBeenCalledWith('Skill pack updated.')
  })

  it('hides the update buttons from non-admins', async () => {
    api.listSkills.mockResolvedValue([pack({ update_available: true, latest_sha: '1234567' })])
    await render(<SkillsView />)
    expect(q('sha-p1')).not.toBeNull()
    expect(q('check-update-p1')).toBeNull()
  })
})
