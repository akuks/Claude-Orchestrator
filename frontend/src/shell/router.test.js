import { afterEach, describe, expect, it, vi } from 'vitest'
import { createElement } from 'react'
import { createRoot } from 'react-dom/client'
import { act } from 'react'
import { PAGES, parseHash, redirectIfForbidden, returnTo, toHash, useRoute } from './router'

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

describe('navigate history handling', () => {
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

  it('push adds one entry and updates the route without waiting for hashchange', () => {
    window.location.hash = '#/usage'
    const seen = mount()
    const before = window.history.length
    act(() => seen.navigate({ page: 'tasks' }))
    expect(window.history.length).toBe(before + 1)
    expect(window.location.hash).toBe('#/tasks')
    expect(seen.route).toEqual({ page: 'tasks' })
  })

  it('replace does not grow history and updates the route', () => {
    window.location.hash = '#/tasks/7'
    const seen = mount()
    const before = window.history.length
    act(() => seen.navigate({ page: 'tasks' }, { replace: true }))
    expect(window.history.length).toBe(before)
    expect(window.location.hash).toBe('#/tasks')
    expect(seen.route).toEqual({ page: 'tasks' })
  })

  it('opening a task from the list then closing it adds no extra entry and goes back to the list', () => {
    window.location.hash = '#/tasks'
    const seen = mount()
    const before = window.history.length
    act(() => seen.navigate({ page: 'tasks', id: '9' }))
    expect(window.history.length).toBe(before + 1)
    const back = vi.spyOn(window.history, 'back').mockImplementation(() => {})
    act(() => returnTo({ page: 'tasks' }, seen.navigate))
    // The entry before the task is the list itself: step back onto it.
    expect(back).toHaveBeenCalledTimes(1)
    expect(window.history.length).toBe(before + 1)
    back.mockRestore()
  })

  it('closing a deep-linked task replaces it with the list', () => {
    window.history.replaceState(null, '', '#/tasks/42')
    const seen = mount()
    const before = window.history.length
    const back = vi.spyOn(window.history, 'back')
    act(() => returnTo({ page: 'tasks' }, seen.navigate))
    expect(back).not.toHaveBeenCalled()
    expect(window.history.length).toBe(before)
    expect(seen.route).toEqual({ page: 'tasks' })
    back.mockRestore()
  })
})

describe('redirectIfForbidden', () => {
  it('sends members away from admin pages with replace', () => {
    const nav = vi.fn()
    expect(redirectIfForbidden({ page: 'users' }, false, nav)).toBe(true)
    expect(nav).toHaveBeenCalledWith({ page: 'home' }, { replace: true })
    redirectIfForbidden({ page: 'teams' }, false, nav)
    expect(nav).toHaveBeenCalledTimes(2)
  })

  it('leaves admins and ordinary pages alone', () => {
    const nav = vi.fn()
    expect(redirectIfForbidden({ page: 'users' }, true, nav)).toBe(false)
    expect(redirectIfForbidden({ page: 'tasks' }, false, nav)).toBe(false)
    expect(nav).not.toHaveBeenCalled()
  })
})

describe('plain hash links', () => {
  it('tag the new entry with where it came from so returnTo can step back', () => {
    window.history.replaceState(null, '', '#/tasks')
    let root
    const container = document.createElement('div')
    const seen = {}
    function Probe() {
      const [route, navigate] = useRoute()
      seen.route = route
      seen.navigate = navigate
      return null
    }
    root = createRoot(container)
    act(() => root.render(createElement(Probe)))
    // What the browser does for <a href="#/tasks/5">: new entry, no state, hashchange.
    window.history.pushState(null, '', '#/tasks/5')
    act(() => {
      window.dispatchEvent(new HashChangeEvent('hashchange', { oldURL: 'http://x/#/tasks', newURL: 'http://x/#/tasks/5' }))
    })
    expect(seen.route).toEqual({ page: 'tasks', id: '5' })
    expect(window.history.state).toEqual({ coFrom: '#/tasks' })
    const back = vi.spyOn(window.history, 'back').mockImplementation(() => {})
    returnTo({ page: 'tasks' }, seen.navigate)
    expect(back).toHaveBeenCalledTimes(1)
    back.mockRestore()
    act(() => root.unmount())
  })
})
