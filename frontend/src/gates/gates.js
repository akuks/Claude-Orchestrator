// Client copy of the backend command check (backend/app/gates/spec.py), so the
// editor can show a reason before save. The server stays the authority.
const BAD = [
  [';', ';'],
  ['&', '&'],
  ['|', '|'],
  ['>', '>'],
  ['<', '<'],
  ['`', 'a backtick'],
  ['$(', '$('],
]

// Returns null when the command is acceptable, otherwise the reason.
export function checkCommand(cmd) {
  if (typeof cmd !== 'string') return 'a command must be text'
  const out = cmd.trim()
  if (!out) return 'a command cannot be empty'
  if (out.length > 500) return 'a command can be at most 500 characters'
  for (const ch of out) {
    const n = ch.codePointAt(0)
    if (ch === '\n') return 'a command cannot contain a newline'
    if (n < 0x20 || n === 0x7f)
      return `a command cannot contain the control character U+${n.toString(16).toUpperCase().padStart(4, '0')}`
  }
  for (const [tok, label] of BAD) {
    if (out.includes(tok)) return `a command cannot contain ${label}`
  }
  return null
}

export const KIND_OPTIONS = [
  { value: 'agent', label: 'Agents' },
  { value: 'gate', label: 'Command gate' },
  { value: 'approval', label: 'Approval' },
]

export const stageKind = (st) => (st && (st.kind === 'gate' || st.kind === 'approval') ? st.kind : 'agent')

const clean = (list) => (list || []).map((s) => (typeof s === 'string' ? s.trim() : s)).filter(Boolean)

// Form values -> API payload. Agent stages are untouched apart from dropping
// the editor's own `kind: 'agent'`, so old workflows save exactly as before.
export function stagePayload(st) {
  const kind = stageKind(st)
  if (kind === 'agent') {
    const { kind: _k, before, run: _r, retry: _t, timeout_min: _m, message: _g, project: _p, ...rest } = st
    const b = clean(before)
    return b.length ? { ...rest, before: b } : rest
  }
  const base = { name: st.name, kind, before: clean(st.before) }
  if (kind === 'gate') {
    const run = clean(st.run)
    const retry = typeof st.retry === 'string' ? st.retry.trim() : ''
    return {
      ...base,
      project: st.project,
      run,
      ...(run.length === 1 && retry ? { retry } : {}),
      timeout_min: st.timeout_min ?? 60,
    }
  }
  return { ...base, message: st.message || '', ...(st.project ? { project: st.project } : {}) }
}

// API stage -> form values (agent stages get an explicit kind for the select).
export function formStage(st) {
  const kind = stageKind(st)
  if (kind === 'agent') return { ...st, kind }
  return { ...st, kind, before: st.before || [], ...(kind === 'gate' ? { run: st.run?.length ? st.run : [''] } : {}) }
}

export const stageLabel = (st) =>
  st.kind === 'gate' ? `⚙ ${st.name}` : st.kind === 'approval' ? `✋ ${st.name}` : st.name
