// Minimal React render helper for vitest + jsdom (no testing-library dep).
import { act } from 'react'
import { createRoot } from 'react-dom/client'

globalThis.IS_REACT_ACT_ENVIRONMENT = true

export async function render(element) {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  await act(async () => {
    root.render(element)
  })
  return {
    container,
    rerender: (el) => act(async () => root.render(el)),
    unmount: () =>
      act(async () => {
        root.unmount()
        container.remove()
      }),
  }
}

// Let pending promises (and the state updates they trigger) settle.
export const flush = () => act(async () => {})

export function setVisibility(state) {
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => state })
  document.dispatchEvent(new Event('visibilitychange'))
}
