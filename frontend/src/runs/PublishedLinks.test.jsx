import { afterEach, describe, expect, it } from 'vitest'
import { render } from '../home/testRender'
import PublishedLinks from './PublishedLinks'

const ev = (p) => ({ type: 'output', payload: { link: 'https://example.com/doc', tool: 'mcp__notion__create_page', title: null, local_file: null, kept: false, ...p } })
let view
const mount = async (events) => {
  view = await render(<PublishedLinks events={events} />)
  return view.container
}
afterEach(async () => {
  await view?.unmount()
  view = null
})

describe('PublishedLinks', () => {
  it('is hidden with nothing published', async () => {
    const c = await mount([{ type: 'text_output', payload: { text: 'hi' } }])
    expect(c.querySelector('[data-testid="published-links"]')).toBeNull()
  })
  it('shows the title, a safe link, and the file kept in the project', async () => {
    const c = await mount([ev({ title: 'Launch plan', local_file: 'docs/plan.md', kept: true })])
    expect(c.textContent).toContain('Published outside the project')
    expect(c.textContent).toContain('Launch plan')
    const a = c.querySelector('a')
    expect(a.getAttribute('href')).toBe('https://example.com/doc')
    expect(a.getAttribute('target')).toBe('_blank')
    expect(a.getAttribute('rel')).toBe('noopener noreferrer')
    expect(c.textContent).toContain('Copy in the project: docs/plan.md')
    expect(c.querySelector('[data-testid="published-nocopy"]')).toBeNull()
  })
  it('flags a missing copy in amber and names the tool in plain words', async () => {
    const c = await mount([ev()])
    expect(c.querySelector('[data-testid="published-nocopy"]').textContent).toBe('No copy in the project')
    expect(c.textContent).toContain('create page')
    expect(c.textContent).not.toContain('mcp__')
  })
  it('does not make a non-web link clickable', async () => {
    const c = await mount([ev({ link: 'javascript:alert(1)' })])
    expect(c.querySelector('a')).toBeNull()
    expect(c.textContent).toContain('javascript:alert(1)')
  })
  it('skips events whose link is not text, without throwing', async () => {
    const c = await mount([ev({ link: { a: 1 } }), ev({ link: null }), ev({ link: 'https://ok.io/x' })])
    expect(c.querySelectorAll('[data-testid="published-row"]')).toHaveLength(1)
  })
})
