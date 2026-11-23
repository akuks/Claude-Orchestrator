// Task brief helpers (pure). Form values live under `brief.*`.
export const BRIEF_KEYS = [
  'goal', 'can_change', 'cannot_change', 'done', 'stop', 'setup', 'log', 'retries', 'tie_break', 'outputs',
]
const LISTS = ['can_change', 'cannot_change', 'outputs']
const TEXTS = ['goal', 'done', 'stop', 'setup', 'log', 'tie_break']
export const REQUIRED_KEYS = ['goal', 'can_change', 'cannot_change', 'done', 'stop']
export const BRIEF_LABELS = {
  goal: 'Goal', can_change: 'CAN change', cannot_change: 'CANNOT change', done: 'Done', stop: 'Stop',
  setup: 'Setup', log: 'Log', retries: 'Retries', tie_break: 'Tie-break', outputs: 'Outputs',
}

const list = (v) => (Array.isArray(v) ? v.map((s) => String(s).trim()).filter(Boolean) : [])
const text = (v) => (typeof v === 'string' ? v.trim() : '')
const num = (v) => (v === null || v === undefined || v === '' || Number.isNaN(Number(v)) ? null : Number(v))

function filled(v) {
  const x = v || {}
  return (
    TEXTS.some((k) => text(x[k])) || LISTS.some((k) => list(x[k]).length) || num(x.retries) !== null
  )
}

// undefined when every brief field is empty (no brief is sent at all).
export function briefPayload(values) {
  if (!filled(values)) return undefined
  const out = {}
  for (const k of TEXTS) if (text(values[k])) out[k] = text(values[k])
  for (const k of LISTS) {
    const l = list(values[k])
    if (l.length || k === 'can_change' || k === 'cannot_change') out[k] = l
  }
  if (num(values.retries) !== null) out.retries = num(values.retries)
  return out
}

// Required fields still missing (the lists default to []), once any field is filled.
export function briefMissing(values) {
  if (!filled(values)) return []
  const x = values || {}
  return ['goal', 'done', 'stop'].filter((k) => !text(x[k]))
}

export function briefScopeLine(p = {}) {
  const only = Array.isArray(p.file_tools_only) ? p.file_tools_only : []
  const n = Number(p.shell_roots) || 0
  return `Brief: ${n} CANNOT folders also guarded for shell commands${only.length ? `; file tools only: ${only.join(', ')}` : ''}`
}
