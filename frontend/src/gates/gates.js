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

// A safe download name: anything outside letters, digits, space, dot, dash and underscore becomes "_".
export function exportFileName(name) {
  const base = String(name ?? '').replace(/[^A-Za-z0-9 _.-]/g, '_').trim()
  return base ? `${base}.md` : 'workflow.md'
}

// Step types, in plain words. The values are the API's `kind`.
export const KIND_OPTIONS = [
  { value: 'agent', label: 'Agents do the work' },
  { value: 'gate', label: 'Run a check' },
  { value: 'approval', label: 'Ask a person' },
]

export const KIND_HELP = {
  agent: 'Each agent gets its own instructions. Agents in the same step work at the same time.',
  gate: 'The server runs your test or build command. It passes only if the command succeeds.',
  approval: 'The workflow waits until someone approves.',
}

// A workflow run's status, in plain words.
const RUN_STATUS_TEXT = {
  pending: 'Starting',
  running: 'Running',
  paused: 'Waiting for you',
  completed: 'Done',
  failed: 'Failed',
}

export const RUN_STATUS_COLOR = {
  pending: 'default',
  running: 'processing',
  paused: 'warning',
  completed: 'success',
  failed: 'error',
}

export function runStatusText(status) {
  if (RUN_STATUS_TEXT[status]) return RUN_STATUS_TEXT[status]
  const s = String(status || '').replace(/_/g, ' ').trim()
  return s ? s[0].toUpperCase() + s.slice(1) : 'Unknown'
}

// "step 2 of 5" for a run's position.
export function runStepText(run) {
  const n = (run?.stages || []).length
  if (!n) return ''
  const at = Math.min((Number(run.current_stage) || 0) + 1, n)
  return `step ${at} of ${n}`
}

// Append a placeholder to the end of a prompt, with one space between.
export function appendPlaceholder(text, ph) {
  const cur = typeof text === 'string' ? text : ''
  if (!cur) return ph
  return /\s$/.test(cur) ? cur + ph : `${cur} ${ph}`
}

export const stageKind = (st) => (st && (st.kind === 'gate' || st.kind === 'approval') ? st.kind : 'agent')

const clean = (list) => (list || []).map((s) => (typeof s === 'string' ? s.trim() : s)).filter(Boolean)

// Form values -> API payload. Agent stages are untouched apart from dropping
// the editor's own `kind: 'agent'`, so old workflows save exactly as before.
// Output names an agent hands over: lowercase letters, digits and _, starting
// with a letter (the server's rule).
export const OUTPUT_NAME = /^[a-z][a-z0-9_]{0,39}$/
export const MAX_OUTPUTS = 10

// One agent of a step. `outputs` is sent only when it has names, so an agent
// without any saves exactly as before.
function itemPayload(it) {
  if (!it || typeof it !== 'object' || !('outputs' in it)) return it
  const { outputs, ...rest } = it
  const names = clean(outputs)
  return names.length ? { ...rest, outputs: names } : rest
}

// The fix-it agent of a check step, only when one is chosen.
function fixPayload(fix) {
  if (!fix || !fix.agent) return null
  const rounds = Number(fix.rounds)
  return { agent: fix.agent, rounds: rounds >= 1 && rounds <= 3 ? Math.round(rounds) : 1 }
}

const AGENT_DROP = new Set(['kind', 'before', 'run', 'retry', 'timeout_min', 'message', 'project', 'fix'])

export function stagePayload(st) {
  const kind = stageKind(st)
  if (kind === 'agent') {
    // Keys keep their order, so an old step saves byte for byte as before.
    const out = {}
    for (const [k, v] of Object.entries(st)) {
      if (AGENT_DROP.has(k)) continue
      if (k === 'auto_retry') {
        if (v === true) out.auto_retry = true
      } else out[k] = k === 'items' && Array.isArray(v) ? v.map(itemPayload) : v
    }
    const b = clean(st.before)
    if (b.length) out.before = b
    return out
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
      ...(fixPayload(st.fix) ? { fix: fixPayload(st.fix) } : {}),
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

// The workflow's spending limit: sent when set; an explicit null only when a
// saved limit was cleared, so a workflow that never had one saves as before.
export function budgetPayload(value, saved) {
  const n = value === '' || value == null ? null : Number(value)
  if (n != null && Number.isFinite(n)) return { budget_usd: n }
  return saved != null ? { budget_usd: null } : {}
}

// Output names of the steps before `stepIndex`, for the insert buttons:
// [{ step: 1, name: 'plan_path', text: '{{stage1.plan_path}}' }]. Step numbers are 1-based.
export function earlierOutputs(stages, stepIndex) {
  const out = []
  ;(stages || []).slice(0, Math.max(0, stepIndex)).forEach((st, i) => {
    if (stageKind(st) !== 'agent') return
    const seen = new Set()
    for (const it of st?.items || []) {
      for (const n of it?.outputs || []) {
        if (typeof n !== 'string' || !OUTPUT_NAME.test(n) || seen.has(n)) continue
        seen.add(n)
        out.push({ step: i + 1, name: n, text: `{{stage${i + 1}.${n}}}` })
      }
    }
  })
  return out
}

// Server-written lines name a check step "gate" ("Step X (gate) passed",
// "Gate X failed: …"); show them with the word the screens use.
export function plainServerText(text) {
  return String(text ?? '')
    .replace(/\(gate\)/g, '(check)')
    .replace(/^Gate /, 'Check ')
}
