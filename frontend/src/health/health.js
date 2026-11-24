// System health helpers (pure). The backend sends {checked_at, overall, checks[]}.

export const STATUS_ORDER = { problem: 0, attention: 1, ok: 2 }

export const STATUS_WORD = { ok: 'Fine', attention: 'Needs attention', problem: 'Problem' }

// Problems first, then attention, then ok; the server's order within a group.
export function sortChecks(checks = []) {
  return checks
    .map((c, i) => [c, i])
    .sort((a, b) => (STATUS_ORDER[a[0].status] ?? 3) - (STATUS_ORDER[b[0].status] ?? 3) || a[1] - b[1])
    .map(([c]) => c)
}

export function overallOf(checks = []) {
  if (checks.some((c) => c.status === 'problem')) return 'problem'
  if (checks.some((c) => c.status === 'attention')) return 'attention'
  return 'ok'
}

// "Everything looks good" / "2 things need attention" / "1 problem".
export function overallLine(checks = []) {
  const status = overallOf(checks)
  const n = checks.filter((c) => c.status === status).length
  if (status === 'ok') return 'Everything looks good'
  if (status === 'attention') return n === 1 ? '1 thing needs attention' : `${n} things need attention`
  return n === 1 ? '1 problem' : `${n} problems`
}

// A detail value as a short plain string (lists and objects are shown on one line).
export function detailText(v) {
  if (v == null) return '–'
  if (Array.isArray(v)) return v.length ? v.map(detailText).join('; ') : 'none'
  if (typeof v === 'object') return Object.entries(v).map(([k, x]) => `${k.replace(/_/g, ' ')}: ${detailText(x)}`).join(', ')
  if (typeof v === 'boolean') return v ? 'yes' : 'no'
  return String(v)
}

export function detailRows(detail) {
  if (!detail || typeof detail !== 'object') return []
  return Object.entries(detail).map(([k, v]) => [k.replace(/_/g, ' '), detailText(v)])
}
