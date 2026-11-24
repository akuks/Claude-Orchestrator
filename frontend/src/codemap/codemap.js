// Plain words for the code map status (phase 6b). Technical strings live under "Details".
export const SWITCH_LABEL = "Keep a map of this project's code"
export const EXPLAIN = 'Lets tasks look things up in a map of the code instead of reading every file.'

export const NO_HISTORY = "Built — this folder has no saved changes history, so freshness can't be checked"

// Why the last refresh failed, in plain words (the server's `failure`); the raw reason is under Details.
const FAILURE = {
  timeout: 'the tool took too long',
  start: 'the tool could not start',
  error: 'the tool stopped with an error',
}

export function statusLine(st) {
  if (!st || !st.enabled) return ''
  if (!st.server_registered) return 'Add the Code map tool first'
  if (st.status === 'running') return st.sha ? 'Refreshing…' : 'Building…'
  if (st.status === 'failed') return `Last refresh failed — ${FAILURE[st.failure] || FAILURE.error}`
  if (st.status === 'ok' && st.sha) {
    const n = Number(st.behind) || 0
    return n > 0 ? `${n} change${n === 1 ? '' : 's'} behind — refreshing soon` : 'Up to date'
  }
  if (st.status === 'ok') return NO_HISTORY
  return 'Not built yet'
}

// ok -> green dot, failed -> red, anything waiting or behind -> amber, off/unknown -> grey.
export function statusTone(st) {
  if (!st || !st.enabled || !st.server_registered) return 'idle'
  if (st.status === 'failed') return 'problem'
  if (st.status === 'ok' && st.sha) return Number(st.behind) > 0 ? 'attention' : 'ok'
  return 'attention'
}

export const sha7 = (s) => (s ? String(s).slice(0, 7) : '')

export function presetDraft(p) {
  return { name: p.name, command: p.command, args: (p.args || []).join(' '), transport: p.transport || 'stdio' }
}
