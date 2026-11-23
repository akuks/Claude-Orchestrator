// Decision memory UI helpers (pure). Every text here is shown as plain text.

export const KINDS = ['decision', 'rule', 'lesson', 'reference']
export const KIND_COLORS = { decision: 'blue', rule: 'red', lesson: 'gold', reference: 'purple' }
export const LIMITS = { title: 200, why: 2000, see: 300, source: 300, touches: 20, touch: 300 }
export const CHANGED_MSG = 'changed by someone else — reload'
export const INACTIVE_MSG = 'this decision is no longer active'

export const displayId = (n) => `D-${String(n).padStart(3, '0')}`

// Run-log line for a `decision_note` event: payload {ids: [numbers], path}.
export function decisionNoteLine(p = {}) {
  const ids = (Array.isArray(p.ids) ? p.ids : []).map(displayId).join(', ')
  return `Showed ${ids || 'decisions'} before editing ${p.path ?? ''}`
}

// Client checks that mirror the server's (the server stays the judge).
export function checkDecision(v) {
  const title = String(v.title ?? '').trim()
  if (!title) return 'a title is required'
  if (title.length > LIMITS.title) return `the title is at most ${LIMITS.title} characters`
  if (String(v.why ?? '').length > LIMITS.why) return `why is at most ${LIMITS.why} characters`
  if (v.kind === 'reference' && !String(v.see ?? '').trim()) return 'a reference needs a see (where to look)'
  if (String(v.see ?? '').length > LIMITS.see) return `see is at most ${LIMITS.see} characters`
  if (String(v.source ?? '').length > LIMITS.source) return `source is at most ${LIMITS.source} characters`
  const t = v.touches || []
  if (t.length > LIMITS.touches) return `at most ${LIMITS.touches} touches`
  if (t.some((x) => String(x).length > LIMITS.touch)) return `a touches entry is at most ${LIMITS.touch} characters`
  return null
}

// Draft for the project rule editor: never + path, pattern = the first touches
// entry with no wildcard (a trailing /** or /* dropped); none -> '' (the
// editor asks for it). Nothing is saved from here.
export function makeRuleDraft(d) {
  let pattern = ''
  for (const raw of d.touches || []) {
    let t = String(raw)
    if (t.endsWith('/**')) t = t.slice(0, -3)
    else if (t.endsWith('/*')) t = t.slice(0, -2)
    if (t && !/[*?]/.test(t)) {
      pattern = t
      break
    }
  }
  return { kind: 'never', match: 'path', pattern, reason: `${d.display_id || displayId(d.number)} ${d.title}` }
}
