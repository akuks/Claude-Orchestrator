// System health helpers (pure). The backend sends {checked_at, overall, checks[]}.

export const STATUS_ORDER = { problem: 0, attention: 1, ok: 2 }

export const STATUS_WORD = { ok: 'Fine', attention: 'Needs attention', problem: 'Problem', info: 'Not checked' }

// A check that could not run here, which is not a fault: shown grey.
export const shownStatus = (c) => (c?.id === 'guard_plugin' && c?.detail?.available === false ? 'info' : c?.status)

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

// One line per connected tool or code-map project, in plain words (the rest of the detail stays generic).
const SERVER_LINE = (x) =>
  `${x.name}: ${x.status || 'unknown'}${x.version ? `, version ${x.version}` : ''}${
    x.pinned ? `, expected ${x.pinned}${x.version_differs ? ' (differs)' : ''}` : ''}`
const PROJECT_LINE = (x) =>
  `${x.project}: ${x.status}${x.failures ? `, ${x.failures} failed refresh${x.failures === 1 ? '' : 'es'}` : ''}${
    x.error ? `, last error: ${x.error}` : ''}`
const isList = (v, key) => Array.isArray(v) && v.length > 0 && v.every((x) => x && typeof x === 'object' && key in x)

const yn = (v) => (v ? 'yes' : 'no')
const list = (v) => (Array.isArray(v) && v.length ? v.join(', ') : 'none')

// Details written as sentences for the checks that have a plain form; null means "use the generic rows".
function plainRows(id, d) {
  if (id === 'node' && d.version) {
    const needed = d.needed ? ` (needs ${d.needed} or newer)` : ''
    return [['Installed', `${String(d.version).replace(/^v/, '')}${needed}`]]
  }
  if (id === 'code_map') {
    const rows = []
    if (isList(d.projects, 'failures')) rows.push(['Projects', d.projects.map(PROJECT_LINE).join('\n')])
    if ('tool_added' in d) rows.push(['Connected tool added', yn(d.tool_added)])
    if (Array.isArray(d.engines)) rows.push(['Engines set up', list(d.engines)])
    if (d.engine_warning) rows.push(['Warning', d.engine_warning])
    return rows
  }
  if (id === 'mcp') {
    const rows = []
    if (isList(d.servers, 'version_differs')) rows.push(['Connected tools', d.servers.map(SERVER_LINE).join('\n')])
    if ('start_in_project' in d) rows.push(['Started in the project folder', String(d.start_in_project)])
    return rows
  }
  if (id === 'agent_tokens' && 'accepted_for_its_task' in d) {
    return [
      ['Works for its own task', yn(d.accepted_for_its_task)],
      ['Works for another task', yn(d.accepted_for_another_task)],
      ['Refused after it is revoked', yn(d.refused_after_revoke)],
    ]
  }
  if (id === 'guard_plugin') {
    if (d.available === false) return []
    return [['Exit code', d.exit_code == null ? '–' : String(d.exit_code)], ['Answer', d.answer || '–']].filter(([, v]) => v !== '–')
  }
  return null
}

export function detailRows(detail, id) {
  if (!detail || typeof detail !== 'object') return []
  const plain = id ? plainRows(id, detail) : null
  if (plain) return plain
  return Object.entries(detail)
    .filter(([k, v]) => !(k === 'engine_warning' && !v))
    .map(([k, v]) => {
      const label = k.replace(/_/g, ' ')
      if (k === 'servers' && isList(v, 'version_differs')) return [label, v.map(SERVER_LINE).join('\n')]
      if (k === 'projects' && isList(v, 'failures')) return [label, v.map(PROJECT_LINE).join('\n')]
      return [label, detailText(v)]
    })
}

// "12 MB", "340 KB" (decimal, rounded) for the tidying preview.
export function sizeText(bytes) {
  const b = Number(bytes) || 0
  if (b >= 1e9) return `${(b / 1e9).toFixed(1)} GB`
  if (b >= 1e6) return `${Math.round(b / 1e6)} MB`
  if (b >= 1e3) return `${Math.round(b / 1e3)} KB`
  return `${b} bytes`
}

const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`

// "3 tasks could be tidied (about 12 MB of history)" / "Nothing to tidy yet".
export function tidyPreviewLine(r) {
  const n = Number(r?.tasks) || 0
  if (!n) return 'Nothing to tidy yet'
  return `${plural(n, 'task', 'tasks')} could be tidied (about ${sizeText(r.bytes)} of history)`
}
