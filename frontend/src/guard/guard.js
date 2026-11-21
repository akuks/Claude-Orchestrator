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
  'sql-destroy': 'Delete database tables or rows',
  'db-write': 'Write to a production database',
  'guest-quarantine': "Run a reviewed repo's code",
  'readonly-source': 'Change a read-only source',
}

// Custom rule ids (org.<slug>, project.<slug>, repo.<slug>) read as "Org rule <slug>".
const CUSTOM_SCOPE = { org: 'Org rule', project: 'Project rule', repo: 'Repo rule' }
export const ruleName = (id) => {
  if (RULE_NAMES[id]) return RULE_NAMES[id]
  const s = String(id)
  const dot = s.indexOf('.')
  if (dot > 0 && CUSTOM_SCOPE[s.slice(0, dot)]) return `${CUSTOM_SCOPE[s.slice(0, dot)]} ${s.slice(dot + 1)}`
  return s
}

// ---- action policy help (Phase 2): shown where each setting lives ----

export const RULES_HELP =
  'Confirm rules pause a matching tool call until a person approves it; never rules block it outright. ' +
  'Text rules match a case-insensitive piece of text, regex rules a Python regular expression (at most 500 ' +
  'characters, checked when you save). Rules are checked against the shell command, the file path of file tools ' +
  'and the input of MCP tools, also when the project\'s built-in rules are off.'
export const SEATBELT_HELP =
  'Never rules are a seatbelt, not a boundary: a command the agent builds at run time (from variables, files ' +
  'or other programs) can get past a text or regex rule. Broad text rules also hold reads, so a rule on ' +
  '".env" also holds reading .env.'
export const RM_RF_DISABLE_HELP =
  'Switching this off also lets the agent run rm -rf on .git and on the project folder itself without asking. ' +
  'Deleting your home folder or the whole disk stays blocked.'
export const LOCKED_HELP =
  "These keep co-guard's own reach (its files, Claude Code settings, git hooks, commands it cannot read, MCP " +
  'policies, read-only sources and quarantine), so they cannot be switched off.'
export const DEV_DB_HELP =
  'Databases matching these patterns count as dev: writes to them never need approval (deleting tables still ' +
  'does). host:<pattern> names database hosts (* is a wildcard, e.g. host:*.dev.internal); mcp:<server> or ' +
  'mcp:<prefix>* names MCP servers; a bare name is an exact host or MCP server name. localhost, sockets and ' +
  'database files are always local.'
export const TRUST_HELP =
  'Own: code made by your org. External: someone else\'s code under review: the agent is told the repo\'s ' +
  'README, comments and scripts are data, not instructions, and the repo\'s rules file is not read. When a ' +
  'project is created or imported, co-guard suggests this from the commit authors\' emails: external when ' +
  'the repo has a git remote and no commit is by an org member\'s git email. A member\'s git email setting ' +
  '(My credentials) therefore influences it.'
export const QUARANTINE_HELP =
  "The repo's install, build, test and run commands need a person's approval, also when the built-in rules " +
  'are off. --version, --help and dry runs are never held.'
export const NO_ANSWER_HELP = {
  deny: 'The call is blocked when nobody answers in time (the agent carries on without it).',
  deny_and_resume:
    'The call is blocked, the agent stops, and the request stays open for 24 h. Approving within 24 h starts a ' +
    'follow-up step that may run it once. The request is dropped if the thread is cancelled.',
}
export const READONLY_HELP =
  'Nothing under these folders may be changed (a hard block that cannot be approved); reads stay free. ' +
  'A path is relative to the project folder or absolute. A read-only folder inside the project also blocks ' +
  'project-wide recursive changes and formatters on "." (rm -rf *, prettier --write ., black .): name the ' +
  'folders to change instead, e.g. src (a tool\'s exclude options are not taken into account).'
export const REFERENCES_HELP =
  'Other projects the agent may read: they are mounted read-only into each task. Only projects the task\'s ' +
  'owner can see are mounted.'
export const RUN_GRANT_HELP = 'These rules, this thread, 12 h'
export const SHARED_KEY_NOTE = 'Bash and Monitor runs of the same command share one approval'
export const QUARANTINE_GRANT_HELP = "Lets this repo's code run in this thread for 12 h"
export const ONCE_ONLY_REASON =
  "This request can't be approved for the whole run (the agent filed it itself, or the command is too long to " +
  'show in full), so only Approve once is offered.'
export const RESUME_BADGE = 'Agent stopped — approving starts a follow-up'
export const RESUME_HELP =
  'Approving within 24 h starts a follow-up step that may run this call once; it is dropped if the thread is ' +
  'cancelled.'
export const REVOKE_NOTE = 'A running step keeps its grants until it ends; revoking stops later steps and new requests.'

// ---- hidden characters ----

// Bidi controls and zero-width characters can make a command read differently
// from what runs. The command block shows each one as a visible marker.
// Also: C0 controls (but tab and newline), C1, line/paragraph separators,
// soft hyphen and other invisible fillers, and Unicode tag characters.
export const HIDDEN_CHARS =
  // eslint-disable-next-line no-control-regex
  /[\u0000-\u0008\u000B-\u001F\u007F-\u009F\u00AD\u034F\u061C\u115F\u180E\u200B-\u200F\u2028\u2029\u202A-\u202E\u2060-\u2064\u2066-\u2069\u3164\uFEFF\uFFA0\u{E0000}-\u{E007F}]/gu
export function revealHidden(text) {
  let count = 0
  const out = String(text ?? '').replace(HIDDEN_CHARS, (ch) => {
    count += 1
    return `⟦U+${ch.codePointAt(0).toString(16).toUpperCase().padStart(4, '0')}⟧`
  })
  return { text: out, count }
}

// ---- approve for this run ----

const HEX64 = /^[0-9a-f]{64}$/
// Whether "Approve for this run" can work for this card: the server lists what a
// run grant would cover (run_keys); a rule keyed to the command needs the
// verified command key, which agent-filed or cut requests lack (409 otherwise).
export function canApproveForRun(card) {
  const keys = Array.isArray(card?.run_keys) ? card.run_keys : []
  if (!keys.length) return false
  return keys.every((k) => k && (k.key_kind !== 'command' || HEX64.test(String(k.key || ''))))
}
export function runGrantTooltip(card) {
  const keys = Array.isArray(card?.run_keys) ? card.run_keys : []
  if (keys.length && keys.every((k) => k.rule_id === 'guest-quarantine')) return QUARANTINE_GRANT_HELP
  const names = [...new Set(keys.map((k) => ruleName(k.rule_id)))]
  const extra = keys.some((k) => k.rule_id === 'guest-quarantine') ? `; ${QUARANTINE_GRANT_HELP.toLowerCase()}` : ''
  const shared = keys.some((k) => k.key_kind === 'command') ? `. ${SHARED_KEY_NOTE}` : ''
  return `${RUN_GRANT_HELP}${names.length ? ` (${names.join(', ')})` : ''}${extra}${shared}`
}
// What a grant covers, in words.
export function grantCovers(g) {
  if (g?.key_kind === 'rule') return g.rule_id === 'guest-quarantine' ? "all of this repo's code" : 'every call'
  if (g?.key_kind === 'tool') return `the tool ${revealHidden(g.key).text}`
  return 'the same command'
}

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

// Short one-line label of a request: "Bash: git reset --hard". Bidi and
// zero-width characters are made visible (Home rows, run-log lines).
export function actionTitle(a) {
  const what = firstLine(a?.summary)
  const title = a?.tool ? (what && what !== a.tool ? `${a.tool}: ${what}` : a.tool) : what || 'Action'
  return revealHidden(title).text
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
// watchdog | ran | granted | filed | info.  ctx: {approvals: Map(id → card), names: Map(userId → name)}
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
  if (ev === 'references') {
    const n = Number(p.count) || 0
    return { kind: 'info', text: `co-guard: ${n} reference project${n === 1 ? '' : 's'} mounted read-only` }
  }
  if (ev === 'reference_skipped')
    return { kind: 'override', text: `co-guard: a reference project was not mounted (${p.reason || 'skipped'})` }
  if (ev === 'readonly_skipped')
    return { kind: 'override', text: `co-guard: read-only path ${p.path || ''} skipped (${p.reason || 'skipped'})` }
  if (ev === 'rules_file') {
    const errs = Array.isArray(p.errors) ? p.errors : []
    const where = errs.map((e) => (e && e.line ? `line ${e.line}: ${e.message}` : e?.message)).filter(Boolean)
    return {
      kind: 'override',
      text: `co-guard: repo rules file lines ignored${where.length ? ` — ${where.slice(0, 5).join('; ')}` : ''}`,
    }
  }
  if (ev === 'PreToolUse') {
    const call = callText(p) || 'a tool call'
    const rules = rulesText(p.rule_ids)
    const status = p.status
    if (p.decision === 'granted') {
      const ids = Array.isArray(p.grant_ids) && p.grant_ids.length ? ` (grant ${p.grant_ids.join(', ')})` : ''
      return { kind: 'granted', text: `${call}${rules} — allowed by an earlier approval${ids}` }
    }
    if (status === 'filed') {
      const card = reqId ? ctx?.approvals?.get(reqId) : null
      return {
        kind: 'filed',
        requestId: reqId,
        canDecide: Boolean(card?.can_decide),
        text: `${call}${rules} — filed for a person; the agent stopped. Approving within 24 h starts a follow-up`,
      }
    }
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
  const resume = card.delivery === 'resume'
  return {
    kind: 'waiting',
    requestId: card.id,
    canDecide: Boolean(card.can_decide),
    text: `${actionTitle(card)}${rulesText(card.rule_ids)} — ${
      card.can_decide ? 'waiting for approval' : 'waiting for a maintainer to decide'
    }${resume ? ' (the agent stopped; approving starts a follow-up)' : ''}`,
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
