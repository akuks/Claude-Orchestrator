import { describe, expect, it, vi } from 'vitest'
import { createElement } from 'react'
import { createRoot } from 'react-dom/client'
import { act } from 'react'
import { returnTo, useRoute } from './router'

// Its own file: jsdom queues hashchange events from other tests' hash writes,
// and this test needs a clean, real popstate → hashchange sequence.
globalThis.IS_REACT_ACT_ENVIRONMENT = true

function mountProbe() {
  const seen = {}
  function Probe() {
    const [route, navigate] = useRoute()
    seen.route = route
    seen.navigate = navigate
    return null
  }
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  act(() => root.render(createElement(Probe)))
  seen.unmount = () => {
    act(() => root.unmount())
    container.remove()
  }
  return seen
}

// Real traversal: jsdom's history.back() is async and fires popstate + hashchange.
async function realBack() {
  const from = window.location.hash
  const events = []
  const log = (e) => events.push(e.type)
  window.addEventListener('popstate', log)
  window.addEventListener('hashchange', log)
  window.history.back()
  await act(async () => {
    for (let i = 0; i < 50 && (window.location.hash === from || !events.includes('hashchange')); i++)
      await new Promise((r) => setTimeout(r, 10))
  })
  window.removeEventListener('popstate', log)
  window.removeEventListener('hashchange', log)
  return events
}

describe('Back/Forward traversal', () => {
  it('does not tag a deep-linked entry, so closing it replaces instead of going back', async () => {
    window.history.replaceState(null, '', '#/tasks/42') // deep link: no state
    const seen = mountProbe()
    act(() => seen.navigate({ page: 'tasks' })) // in-app push
    expect(seen.route).toEqual({ page: 'tasks' })
    const events = await realBack() // Back onto the deep-linked task
    expect(events).toEqual(['popstate', 'hashchange'])
    expect(window.location.hash).toBe('#/tasks/42')
    expect(seen.route).toEqual({ page: 'tasks', id: '42' })
    expect(window.history.state).toBeNull()

    const len = window.history.length
    const back = vi.spyOn(window.history, 'back')
    act(() => returnTo({ page: 'tasks' }, seen.navigate))
    expect(back).not.toHaveBeenCalled()
    expect(window.history.length).toBe(len)
    expect(window.location.hash).toBe('#/tasks')
    expect(seen.route).toEqual({ page: 'tasks' })
    back.mockRestore()
    seen.unmount()
  })
})

