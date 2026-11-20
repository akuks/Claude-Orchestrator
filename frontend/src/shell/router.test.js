import { afterEach, describe, expect, it } from 'vitest'
import { createElement } from 'react'
import { createRoot } from 'react-dom/client'
import { act } from 'react'
import { PAGES, parseHash, toHash, useRoute } from './router'

globalThis.IS_REACT_ACT_ENVIRONMENT = true

const ROUTES = [
  '#/home',
  '#/tasks',
  '#/tasks/abc-123',
  '#/runs/r1',
  '#/projects',
  '#/automation',
  '#/approvals',
  '#/mcp',
  '#/usage',
  '#/security',
  '#/users',
  '#/teams',
]

describe('parseHash', () => {
  it('reads a page and id', () => {
    expect(parseHash('#/runs/abc')).toEqual({ page: 'runs', id: 'abc' })
    expect(parseHash('#/tasks/42')).toEqual({ page: 'tasks', id: '42' })
    expect(parseHash('#/tasks')).toEqual({ page: 'tasks' })
  })

  it('falls back to home for unknown, empty or malformed hashes', () => {
    expect(parseHash('#/nope')).toEqual({ page: 'home' })
    expect(parseHash('')).toEqual({ page: 'home' })
    expect(parseHash('#')).toEqual({ page: 'home' })
    expect(parseHash(undefined)).toEqual({ page: 'home' })
    expect(parseHash('#/runs')).toEqual({ page: 'home' })
    expect(parseHash('#/tasks/%E0%A4%A')).toEqual({ page: 'tasks' })
  })

  it('ignores ids on pages that take none', () => {
    expect(parseHash('#/projects/7')).toEqual({ page: 'projects' })
  })

  it('round-trips every route', () => {
    for (const h of ROUTES) expect(toHash(parseHash(h))).toBe(h)
  })

  it('knows every page in the route list', () => {
    expect(PAGES).toEqual([
      'home', 'tasks', 'runs', 'projects', 'automation', 'approvals', 'mcp', 'usage', 'security', 'users', 'teams',
    ])
  })
})

describe('toHash', () => {
  it('encodes ids', () => {
    expect(toHash({ page: 'tasks', id: 'a b' })).toBe('#/tasks/a%20b')
    expect(parseHash(toHash({ page: 'tasks', id: 'a b' }))).toEqual({ page: 'tasks', id: 'a b' })
  })
})

describe('useRoute', () => {
  let container, root
  afterEach(() => {
    act(() => root?.unmount())
    container?.remove()
  })

  function mount() {
    const seen = {}
    function Probe() {
      const [route, navigate] = useRoute()
      seen.route = route
      seen.navigate = navigate
      return null
    }
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
    act(() => root.render(createElement(Probe)))
    return seen
  }

  const fireHash = (h) =>
    act(() => {
      window.location.hash = h
      window.dispatchEvent(new HashChangeEvent('hashchange'))
    })

  it('follows hashchange (back/forward) and navigates by route or hash', async () => {
    window.location.hash = '#/projects'
    const seen = mount()
    expect(seen.route).toEqual({ page: 'projects' })

    act(() => seen.navigate({ page: 'tasks', id: '9' }))
    expect(window.location.hash).toBe('#/tasks/9')
    await act(async () => {
      window.dispatchEvent(new HashChangeEvent('hashchange'))
    })
    expect(seen.route).toEqual({ page: 'tasks', id: '9' })

    act(() => seen.navigate('#/usage'))
    expect(window.location.hash).toBe('#/usage')

    // Back button: the browser rewrites the hash and fires hashchange.
    fireHash('#/projects')
    expect(seen.route).toEqual({ page: 'projects' })
  })
})
