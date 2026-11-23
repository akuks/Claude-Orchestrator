import { afterEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { flush, render } from '../home/testRender'

vi.mock('../api', () => ({
  api: { skillTemplate: vi.fn(), lintSkillText: vi.fn() },
}))

import { api } from '../api'
import SkillLint, { PackResults } from './SkillLint'

const click = async (el) => { await act(async () => { el.dispatchEvent(new MouseEvent('click', { bubbles: true })) }); await flush() }
const q = (id) => document.querySelector(`[data-testid="${id}"]`)

afterEach(() => { document.body.innerHTML = ''; vi.clearAllMocks() })

describe('SkillLint', () => {
  it('shows problems by line, as text', async () => {
    api.lintSkillText.mockResolvedValue({ ok: false, problems: [
      { line: 3, message: 'description must start with "Use when" <img src=x onerror=alert(1)>' },
      { line: 6, message: 'part is missing Output:' },
    ] })
    await render(<SkillLint />)
    await click(document.querySelector('.ant-collapse-header'))
    await click(q('lint-run'))
    const items = [...document.querySelectorAll('[data-testid="lint-problem"]')]
    expect(items.map((i) => i.textContent.slice(0, 7))).toEqual(['Line 3:', 'Line 6:'])
    expect(items[1].textContent).toContain('part is missing Output:')
    expect(document.querySelector('[data-testid="lint-problems"] img')).toBeNull()
    expect(api.lintSkillText).toHaveBeenCalledWith('')
  })

  it('shows the template and says when there are no problems', async () => {
    api.skillTemplate.mockResolvedValue({ text: '---\nname: t\n---' })
    api.lintSkillText.mockResolvedValue({ ok: true, problems: [] })
    await render(<SkillLint />)
    await click(document.querySelector('.ant-collapse-header'))
    await click(q('show-template'))
    expect(q('template-text').textContent).toContain('name: t')
    await click(q('use-template'))
    expect(q('lint-input').value).toContain('name: t')
    await click(q('lint-run'))
    expect(q('lint-ok')).not.toBeNull()
  })

  it('shows a lint error', async () => {
    api.lintSkillText.mockRejectedValue(new Error('text is over 256 KB'))
    await render(<SkillLint />)
    await click(document.querySelector('.ant-collapse-header'))
    await click(q('lint-run'))
    expect(q('lint-error').textContent).toContain('over 256 KB')
  })

  it('renders pack results per file', async () => {
    await render(<PackResults results={[{ file: 'a/SKILL.md', problems: [{ line: 1, message: 'file too large' }] }]} />)
    expect(q('pack-lint-file').textContent).toContain('a/SKILL.md')
    expect(q('lint-problem').textContent).toBe('Line 1: file too large')
  })
})
