import { useCallback, useEffect, useState } from 'react'

// Lightweight hash routing: #/<page>[/<id>]. Hash changes push history
// entries, so the browser's back/forward buttons work without a router library.

export const PAGES = [
  'home',
  'tasks',
  'runs',
  'projects',
  'automation',
  'approvals',
  'mcp',
  'usage',
  'security',
  'users',
  'teams',
]

// Pages addressed by id. `runs` only exists with one.
const ID_PAGES = new Set(['tasks', 'runs'])
const ID_REQUIRED = new Set(['runs'])

export const DEFAULT_ROUTE = Object.freeze({ page: 'home' })

function decode(s) {
  try {
    return decodeURIComponent(s)
  } catch {
    return null
  }
}

export function parseHash(hash) {
  const path = String(hash ?? '').replace(/^#/, '').replace(/^\/+/, '')
  const [page, rawId] = path.split('/')
  if (!PAGES.includes(page)) return { page: 'home' }
  const id = ID_PAGES.has(page) && rawId ? decode(rawId) : null
  if (ID_REQUIRED.has(page) && !id) return { page: 'home' }
  return id ? { page, id } : { page }
}

export function toHash({ page, id } = DEFAULT_ROUTE) {
  const p = PAGES.includes(page) ? page : 'home'
  return id != null && id !== '' ? `#/${p}/${encodeURIComponent(id)}` : `#/${p}`
}

const currentHash = () => (typeof window === 'undefined' ? '' : window.location.hash)

// [route, navigate]. navigate accepts a route object or a hash string.
export function useRoute() {
  const [route, setRoute] = useState(() => parseHash(currentHash()))

  useEffect(() => {
    const sync = () => {
      const next = parseHash(currentHash())
      setRoute((prev) => (prev.page === next.page && prev.id === next.id ? prev : next))
    }
    window.addEventListener('hashchange', sync)
    sync()
    return () => window.removeEventListener('hashchange', sync)
  }, [])

  const navigate = useCallback((to) => {
    const hash = typeof to === 'string' ? toHash(parseHash(to)) : toHash(to)
    if (window.location.hash !== hash) window.location.hash = hash // fires hashchange
  }, [])

  return [route, navigate]
}
