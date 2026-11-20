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
// approvals/<id>: an action approval card.
const ID_PAGES = new Set(['tasks', 'runs', 'approvals'])
const ID_REQUIRED = new Set(['runs'])

export const DEFAULT_ROUTE = Object.freeze({ page: 'home' })

function decode(s) {
  try {
    return decodeURIComponent(s)
  } catch {
    return null
  }
}

// A run page may carry the selected stage: #/runs/<id>?stage=N (N ≥ 0), so
// Back/Forward restores the selection.
const STAGE_RE = /(?:^|&)stage=(\d+)(?:&|$)/

export function parseHash(hash) {
  const raw = String(hash ?? '').replace(/^#/, '').replace(/^\/+/, '')
  const q = raw.indexOf('?')
  const path = q === -1 ? raw : raw.slice(0, q)
  const query = q === -1 ? '' : raw.slice(q + 1)
  const [page, rawId] = path.split('/')
  if (!PAGES.includes(page)) return { page: 'home' }
  const id = ID_PAGES.has(page) && rawId ? decode(rawId) : null
  if (ID_REQUIRED.has(page) && !id) return { page: 'home' }
  if (!id) return { page }
  const m = page === 'runs' ? query.match(STAGE_RE) : null
  return m ? { page, id, stage: Number(m[1]) } : { page, id }
}

const isStage = (s) => Number.isInteger(s) && s >= 0

export function toHash({ page, id, stage } = DEFAULT_ROUTE) {
  const p = PAGES.includes(page) ? page : 'home'
  if (id == null || id === '') return `#/${p}`
  const q = p === 'runs' && isStage(stage) ? `?stage=${stage}` : ''
  return `#/${p}/${encodeURIComponent(id)}${q}`
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
      setRoute((prev) => (prev.page === next.page && prev.id === next.id && prev.stage === next.stage ? prev : next))
    }
    // Only navigate() tags entries (in-app links go through it, see
    // interceptHashLinks). hashchange/popstate also fire on Back/Forward, where
    // tagging would record the entry being left, so they only re-read the hash.
    window.addEventListener('hashchange', sync)
    window.addEventListener('popstate', sync)
    window.addEventListener(SYNC_EVENT, sync)
    sync()
    return () => {
      window.removeEventListener('hashchange', sync)
      window.removeEventListener('popstate', sync)
      window.removeEventListener(SYNC_EVENT, sync)
    }
  }, [])

  const navigate = useCallback((to, { replace = false } = {}) => {
    const hash = typeof to === 'string' ? toHash(parseHash(to)) : toHash(to)
    const from = currentHash()
    if (from === hash) return
    // Keep the entry's origin (if any).
    if (replace) window.history.replaceState(window.history.state, '', hash)
    else window.history.pushState({ coFrom: from }, '', hash)
    window.dispatchEvent(new Event(SYNC_EVENT))
  }, [])

  return [route, navigate]
}

// Route plain left-clicks on in-app links (<a href="#/…">) through navigate,
// so the new entry is pushed with its origin. Modified/middle clicks, targets
// and anything a handler already prevented keep the browser default.
export function interceptHashLinks(navigate, root = document) {
  const onClick = (e) => {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return
    const a = e.target?.closest?.('a[href]')
    if (!a || (a.target && a.target !== '_self') || a.hasAttribute('download')) return
    const href = a.getAttribute('href')
    if (!href?.startsWith('#/')) return
    e.preventDefault()
    navigate(href)
  }
  root.addEventListener('click', onClick)
  return () => root.removeEventListener('click', onClick)
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
