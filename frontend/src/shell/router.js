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
const SYNC_EVENT = 'co:route'

// [route, navigate]. navigate(routeOrHash, { replace }) writes history directly
// (pushState / replaceState, which don't fire hashchange) and then tells every
// useRoute to re-read the hash. Each pushed entry remembers the hash it came
// from (state.coFrom) so returnTo() can step back instead of stacking entries.
export function useRoute() {
  const [route, setRoute] = useState(() => parseHash(currentHash()))

  useEffect(() => {
    const sync = () => {
      const next = parseHash(currentHash())
      setRoute((prev) => (prev.page === next.page && prev.id === next.id ? prev : next))
    }
    // A plain <a href="#/…"> click creates an entry without state: tag it with
    // where it came from, the same as navigate() does.
    const onHashChange = (e) => {
      if (window.history.state == null && e?.oldURL) {
        const i = e.oldURL.indexOf('#')
        window.history.replaceState({ coFrom: i >= 0 ? e.oldURL.slice(i) : '' }, '')
      }
      sync()
    }
    window.addEventListener('hashchange', onHashChange)
    window.addEventListener('popstate', sync)
    window.addEventListener(SYNC_EVENT, sync)
    sync()
    return () => {
      window.removeEventListener('hashchange', onHashChange)
      window.removeEventListener('popstate', sync)
      window.removeEventListener(SYNC_EVENT, sync)
    }
  }, [])

  const navigate = useCallback((to, { replace = false } = {}) => {
    const hash = typeof to === 'string' ? toHash(parseHash(to)) : toHash(to)
    const from = currentHash()
    if (from === hash) return
    // Keep the entry's origin; a non-null state also stops hashchange from re-tagging it.
    if (replace) window.history.replaceState(window.history.state ?? { coFrom: null }, '', hash)
    else window.history.pushState({ coFrom: from }, '', hash)
    window.dispatchEvent(new Event(SYNC_EVENT))
  }, [])

  return [route, navigate]
}

// Leave the current entry for `route`: if we got here straight from `route`,
// go back to it (no new entry, Back keeps working); otherwise (deep link,
// reload) replace the current entry with it.
export function returnTo(route, navigate) {
  if (window.history.state?.coFrom === toHash(route)) window.history.back()
  else navigate(route, { replace: true })
}

const ADMIN_PAGES = new Set(['users', 'teams'])

// Members following a link to an admin page land on Home. Replace, so Back
// doesn't bounce into the redirect again.
export function redirectIfForbidden(route, isAdmin, navigate) {
  if (isAdmin || !ADMIN_PAGES.has(route.page)) return false
  navigate({ page: 'home' }, { replace: true })
  return true
}
