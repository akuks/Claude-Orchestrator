import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api'

// Data behind Home: what needs the viewer, live workflows, their recent work
// and today's cost. Every source is fetched independently (one failing never
// blanks the page) and polled every 10s while the tab is visible.

export const POLL_MS = 10_000
const DAY_MS = 24 * 3600 * 1000
const FINISHED = new Set(['completed', 'failed', 'cancelled'])

// Backend timestamps are UTC; SQLite drops the offset, so a bare ISO string
// is read as UTC rather than local time.
export function parseTime(d) {
  if (!d) return null
  const s = typeof d === 'string' && /T\d{2}:\d{2}(:\d{2}(\.\d+)?)?$/.test(d) ? `${d}Z` : d
  const t = new Date(s).getTime()
  return Number.isFinite(t) ? t : null
}
const ts = parseTime

// ---- pure selectors -------------------------------------------------------

// Items for the "Needs you" panel, in display order: missing credential,
// approvals (the server already filters to ones the viewer can decide), own
// failed tasks from the last 24h, own paused schedules.
export function selectNeedsYou({ approvals, failedTasks, schedules, creds, me, now = Date.now() } = {}) {
  const meId = me?.id
  const items = []
  if (creds?.claude === 'none')
    items.push({
      key: 'credential',
      kind: 'credential',
      id: null,
      title: 'Add your Claude credential',
      detail: 'Tasks you start fail until you add a token or API key',
    })
  for (const a of approvals || [])
    items.push({
      key: `approval:${a.id}`,
      kind: 'approval',
      id: a.id,
      title: a.title,
      detail: a.project || null,
      risk: a.risk,
      at: a.created_at,
    })
  for (const t of failedTasks || []) {
    if (!meId || t.created_by !== meId) continue
    const at = ts(t.completed_at) ?? ts(t.created_at)
    if (at == null || now - at > DAY_MS) continue
    items.push({
      key: `failed:${t.id}`,
      kind: 'failed',
      id: t.id,
      title: t.title,
      detail: t.error || t.project || null,
      at: t.completed_at || t.created_at,
    })
  }
  for (const s of schedules || []) {
    if (!meId || s.created_by !== meId || !s.blocked_reason) continue
    items.push({
      key: `schedule:${s.id}`,
      kind: 'schedule',
      id: s.id,
      title: s.name,
      detail: `Paused: ${s.blocked_reason}`,
    })
  }
  return items
}

// The viewer's own last finished threads (list rows are thread roots).
export function selectRecent(tasks, me, n = 5) {
  return (tasks || [])
    .filter((t) => me?.id && t.created_by === me.id && FINISHED.has(t.status))
    .sort((a, b) => (ts(b.completed_at) ?? ts(b.created_at) ?? 0) - (ts(a.completed_at) ?? ts(a.created_at) ?? 0))
    .slice(0, n)
}

export function costToday(usage) {
  const c = usage?.today?.cost_usd
  return typeof c === 'number' ? c : null
}

export function displayName(user) {
  if (user?.name?.trim()) return user.name.trim()
  if (user?.email) return user.email.split('@')[0]
  return 'there'
}

export function greetingFor(date = new Date()) {
  const h = date.getHours()
  if (h >= 5 && h < 12) return 'Good morning'
  if (h >= 12 && h < 18) return 'Good afternoon'
  return 'Good evening'
}

// ---- hook -------------------------------------------------------------------

const SOURCES = {
  approvals: () => api.listApprovals(),
  failedTasks: () => api.listTasks({ status: 'failed', limit: 50 }),
  schedules: () => api.listSchedules(),
  creds: () => api.myCredentials(),
  live: () => api.liveRuns(),
  tasks: () => api.listTasks({ limit: 100 }),
  usage: () => api.usageSummary(),
}
// Which panel a failing source belongs to.
const SECTION = {
  approvals: 'needsYou',
  failedTasks: 'needsYou',
  schedules: 'needsYou',
  creds: 'needsYou',
  live: 'live',
  tasks: 'recent',
  usage: 'stats',
}

const isHidden = () => typeof document !== 'undefined' && document.visibilityState === 'hidden'

// `refreshSignal`: any value whose change should trigger an immediate reload
// (e.g. the viewer's credential status after editing My credentials).
export function useHomeData(user, { refreshSignal, intervalMs = POLL_MS } = {}) {
  const [raw, setRaw] = useState({})
  const [errors, setErrors] = useState({})
  const [loading, setLoading] = useState(true)
  const [now, setNow] = useState(() => Date.now())
  const alive = useRef(true)
  const inFlight = useRef(null)

  const load = useCallback(() => {
    if (inFlight.current) return inFlight.current
    const keys = Object.keys(SOURCES)
    const p = Promise.allSettled(keys.map((k) => SOURCES[k]())).then((results) => {
      inFlight.current = null
      if (!alive.current) return
      const next = {}
      const errs = {}
      results.forEach((r, i) => {
        const k = keys[i]
        if (r.status === 'fulfilled') next[k] = r.value
        else errs[SECTION[k]] = errs[SECTION[k]] || r.reason?.message || 'Request failed'
      })
      // A failed source keeps its last good value.
      setRaw((prev) => ({ ...prev, ...next }))
      setErrors(errs)
      setNow(Date.now())
      setLoading(false)
    })
    inFlight.current = p
    return p
  }, [])

  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
    }
  }, [])

  useEffect(() => {
    load()
  }, [load, refreshSignal])

  // Poll while visible; catch up as soon as the tab comes back.
  useEffect(() => {
    const id = setInterval(() => {
      if (!isHidden()) load()
    }, intervalMs)
    const onVisible = () => {
      if (!isHidden()) load()
    }
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      clearInterval(id)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [load, intervalMs])

  const needsYou = useMemo(
    () =>
      selectNeedsYou({
        approvals: raw.approvals,
        failedTasks: raw.failedTasks,
        schedules: raw.schedules,
        creds: raw.creds,
        me: user,
        now,
      }),
    [raw.approvals, raw.failedTasks, raw.schedules, raw.creds, user, now],
  )
  const recent = useMemo(() => selectRecent(raw.tasks, user), [raw.tasks, user])
  const live = raw.live || []
  const stats = useMemo(
    () => ({ costToday: costToday(raw.usage), liveCount: raw.live ? raw.live.length : null }),
    [raw.usage, raw.live],
  )
  const error = Object.values(errors)[0] || null

  return { needsYou, live, recent, stats, loading, error, errors, reload: load }
}
