import { parseTime } from '../time'

// co-guard UI helpers (pure): rule names, countdowns, settings help, and the
// run-log line for a `guard` task event. Every text here is shown as plain
// text (React escapes it); request fields may end with
// "… (truncated, N chars)".

// Friendly names for co-guard rule ids (backend/app/guard/rules.py RULES).
export const RULE_NAMES = {
  'rm-rf-outside': 'Delete outside the project',
  'git-force-push-main': 'Force-push to main',
  'git-reset-hard': 'Discard uncommitted work',
  'git-clean-fdx': 'Delete untracked files',
  'git-stash-drop': 'Drop git stash',
  'secret-files': 'Secret files',
  'chmod-wide': 'Wide file permissions',
  'pipe-to-shell': 'Run a script from the internet',
  'git-internals': 'Git config or hooks',
  'agent-config': 'Claude Code settings',
  'uncertain-command': 'Unclear command',
  'self-protect': 'co-guard self-protection',
  unparseable: 'Too complex to check',
  'mcp-require-approval': 'MCP tool needs approval',
  'mcp-block': 'MCP tool blocked',
  'unsupported-shell': 'Unsupported shell',
  'unknown-tool': 'Unknown tool',
}

export const ruleName = (id) => RULE_NAMES[id] || String(id)

// Mirrors backend runtime.GUARD_SETTINGS_HELP (keep the two in step).
export const GUARD_SETTINGS_HELP =
  "co-guard checks every tool call before it runs: dangerous commands are blocked or need a person's " +
  'approval, and MCP tools follow their policies. Turning the guard off switches off the built-in command ' +
  'rules only: MCP approvals and blocks, and self-protection of the orchestrator\'s own files, always apply. ' +
  'Inline !`cmd` in skills, slash commands and skill packs is disabled for every task, because it would run ' +
  'without that check.'

// Who may change a project's guard settings: its maintainers and admins.
export const canEditGuard = (project, user) => project?.my_role === 'maintainer' || user?.role === 'admin'

export const WAIT_MIN = 60
export const WAIT_MAX = 900
// The server closes decisions this long before expires_at (routers/guard.py DECIDE_MARGIN).
export const DECIDE_MARGIN_MS = 5000

// ms until decisions on this request close.
export function closesIn(card, now = Date.now()) {
  const exp = parseTime(card?.expires_at)
  return exp == null ? 0 : exp - DECIDE_MARGIN_MS - now
}

export function formatLeft(ms) {
  const s = Math.max(0, Math.floor(ms / 1000))
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

export function formatWait(seconds) {
  const s = Math.round(Number(seconds) || 0)
  const m = Math.floor(s / 60)
  const r = s % 60
  if (!m) return `${r} s`
  return r ? `${m} min ${r} s` : `${m} min`
}

const firstLine = (s, n = 160) => {
  const line = String(s ?? '').split('\n')[0]
  return line.length > n ? `${line.slice(0, n - 1)}…` : line
}

// Short one-line label of a request: "Bash: git reset --hard".
export function actionTitle(a) {
  const what = firstLine(a?.summary)
  return a?.tool ? (what && what !== a.tool ? `${a.tool}: ${what}` : a.tool) : what || 'Action'
}

const rulesText = (ids) => (Array.isArray(ids) && ids.length ? ` [${ids.map(ruleName).join(', ')}]` : '')
const callText = (p) => [p.tool, p.summary && p.summary !== p.tool ? p.summary : null].filter(Boolean).join(': ')

function who(p, ctx) {
  const card = p.request_id ? ctx?.approvals?.get(p.request_id) : null
  const id = card?.decided_by
  const name = id ? ctx?.names?.get(id) || id : null
  return { card, name }
}

// A `guard` event payload (worker.guard_payload / runtime events) → line.
// kinds: blocked | waiting | approved | rejected | expired | override | error |
// watchdog | ran | info.  ctx: {approvals: Map(id → card), names: Map(userId → name)}
export function guardLine(p = {}, ctx = {}) {
  const ev = p.event
  const reqId = p.request_id || null
  if (ev === 'settings_override') {
    const files = Array.isArray(p.files) && p.files.length ? ` (${p.files.join(', ')})` : ''
    return { kind: 'override', text: `co-guard settings override: ${p.reason || 'project settings ignored'}${files}` }
  }
  if (ev === 'watchdog')
    return { kind: 'watchdog', text: `co-guard watchdog stopped the task: ${p.reason || 'a call ran unchecked'}` }
  if (ev === 'unchecked_error')
    return { kind: 'info', text: `${p.tool || 'A tool'} call rejected before co-guard ran (not executed)` }
  if (ev === 'ConfigChange')
    return p.decision === 'blocked'
      ? { kind: 'blocked', text: `Settings change blocked${p.reason ? `: ${p.reason}` : ' (it would switch co-guard off)'}` }
      : { kind: 'info', text: `Settings changed${p.source ? ` (${p.source})` : ''}` }
  if (ev === 'PostToolUse' && p.decision === 'ran')
    return { kind: 'ran', text: `Approved call ran: ${callText(p) || 'tool'}` }
  if (ev === 'PreToolUse') {
    const call = callText(p) || 'a tool call'
    const rules = rulesText(p.rule_ids)
    const status = p.status
    if (status === 'approved' || p.decision === 'approved') {
      const { name } = who(p, ctx)
      return { kind: 'approved', requestId: reqId, text: `${call}${rules} — approved by ${name || 'a person'}` }
    }
    if (status === 'rejected') {
      const { card, name } = who(p, ctx)
      const why = card?.decision_reason && card.decision_reason !== 'Rejected' ? `: ${card.decision_reason}` : ''
      return { kind: 'rejected', requestId: reqId, text: `${call}${rules} — rejected by ${name || 'a person'}${why}` }
    }
    if (status === 'expired' || status === 'timeout')
      return { kind: 'expired', requestId: reqId, text: `${call}${rules} — approval request expired, blocked` }
    if (status === 'refused')
      return { kind: 'error', requestId: reqId, text: `${call}${rules} — blocked: approval could not be requested (${p.detail || 'refused'})` }
    const reason = String(p.reason || '')
    if (/^(co-guard error|hook watchdog)/.test(reason))
      return { kind: 'error', text: `co-guard error — ${call === 'a tool call' ? 'action' : call} blocked for safety` }
    return { kind: 'blocked', text: `Blocked ${call}${rules}${reason ? ` — ${reason}` : ''}` }
  }
  const bits = [p.agent_type, p.source, p.reason].filter(Boolean).join(', ')
  return { kind: 'info', text: `${ev || 'co-guard'}${bits ? ` (${bits})` : ''}` }
}

// A pending request of the run, shown while it waits for a person.
export function waitingLine(card) {
  return {
    kind: 'waiting',
    requestId: card.id,
    text: `${actionTitle(card)}${rulesText(card.rule_ids)} — waiting for approval`,
  }
}

// Rail badge / Home count: pending task approvals + action approvals the
// viewer may decide. A failing source counts 0; null if both failed.
export async function pendingApprovalTotal(api) {
  const res = await Promise.allSettled([api.listApprovals(), api.listActionApprovals()])
  const ok = res.filter((r) => r.status === 'fulfilled')
  if (!ok.length) return null
  return ok.reduce((n, r) => n + (Array.isArray(r.value) ? r.value.length : 0), 0)
}
