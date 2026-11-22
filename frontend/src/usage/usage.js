// Token care helpers (phase 3b): the context and budget chips, the thread's
// "so far" line, the run-log lines of usage events, the project's
// fresh-session setting and the usage poll. Titles and event texts come from
// the agent or the server: callers show them as plain text only.
import { useEffect, useState } from 'react'
import { api } from '../api'
import { revealHidden } from '../guard/guard'

export const FRESH_MIN = 50000
export const FRESH_MAX = 900000
export const FRESH_SUGGEST = 150000
export const USAGE_POLL_MS = 5000

const clean = (v) => revealHidden(v).text
const num = (v) => typeof v === 'number' && Number.isFinite(v)
const kOf = (n) => `${Math.round(n / 1000)}k`
const usd = (v) => `$${(num(v) ? v : 0).toFixed(2)}`

// "context 63k": amber at 80 % of the project's fresh-session threshold, red
// at it; no colour when the project has no threshold.
export function contextChip(step) {
  if (!step || !num(step.context_tokens)) return null
  const n = step.context_tokens
  const limit = num(step.fresh_session_tokens) && step.fresh_session_tokens > 0 ? step.fresh_session_tokens : null
  const level = !limit ? 'none' : n >= limit ? 'red' : n >= 0.8 * limit ? 'amber' : 'none'
  return { text: `context ${kOf(n)}`, level }
}

// "budget 41 % est." of the step's max budget; "est." whenever the server marks
// the cost an estimate (running, or finished without a result cost).
// Hidden without a max budget or a cost figure. Amber at 80 %, red at 95 %
// (the bands co-guard tells the agent about).
export function budgetChip(step) {
  if (!step || !num(step.max_budget_usd) || !num(step.est_cost_usd) || !num(step.budget_pct)) return null
  const pct = step.budget_pct
  const level = pct >= 95 ? 'red' : pct >= 80 ? 'amber' : 'none'
  return { text: `budget ${Math.round(pct)} %${step.cost_is_estimate === true ? ' est.' : ''}`, level }
}

export function fmtDuration(ms) {
  const min = Math.round((num(ms) ? ms : 0) / 60000)
  if (min < 1) return num(ms) && ms > 0 ? '<1 min' : '0 min'
  if (min < 60) return `${min} min`
  return `${Math.floor(min / 60)} h ${min % 60} min`
}

export function fmtTokens(n) {
  const v = num(n) ? n : 0
  if (v >= 1e6) return `${(v / 1e6).toFixed(1)}M tokens`
  if (v >= 1000) return `${kOf(v)} tokens`
  return `${v} tokens`
}

// "4 steps · 41 min · $2.10 · 1.2M tokens — last: <title>, 18 min, $1.40"
export function soFarLine(thread) {
  if (!thread || !num(thread.steps) || thread.steps < 1) return null
  const n = thread.steps
  let line = `${n} step${n === 1 ? '' : 's'} · ${fmtDuration(thread.duration_ms)} · ${usd(thread.cost_usd)} · ${fmtTokens(thread.tokens)}`
  const last = thread.last
  if (last) line += ` — last: ${clean(last.title || 'untitled')}, ${fmtDuration(last.duration_ms)}, ${usd(last.cost_usd)}`
  return line
}

const BAND_TEXT = {
  budget80: ['budget about 80 % used', 'the agent was told to save its notes'],
  budget95: ['budget about 95 % used', 'the agent was told to finish up and end its turn'],
}
const FRESH_TAIL = 'the agent was told to save notes and end its turn; the thread continues in a fresh session'
const AUTO_CONTINUE_LINE = (k) => `continued automatically: context reached ${k}` // server: resume/texts.py

// A run-log line ({kind, text}) for a usage_band or auto_continue event, else null.
export function usageLine(ev) {
  const p = (ev && ev.payload) || {}
  if (ev?.type === 'usage_band') {
    const band = String(p.band || '')
    const value = num(p.value) ? p.value : null
    if (BAND_TEXT[band]) {
      const [head, tail] = BAND_TEXT[band]
      return { kind: band, text: `${head}${value != null ? ` (${usd(value)})` : ''}: ${tail}` }
    }
    if (band === 'fresh') {
      return { kind: 'fresh', text: `context reached ${value != null ? kOf(value) : 'the threshold'}: ${FRESH_TAIL}` }
    }
    return { kind: 'band', text: `usage band ${clean(band)}` }
  }
  if (ev?.type === 'auto_continue') {
    const text = p.text ? clean(p.text) : AUTO_CONTINUE_LINE(num(p.context_tokens) ? kOf(p.context_tokens) : '?')
    return { kind: 'auto', text }
  }
  return null
}

// The project form's fresh-session field: empty is off.
export function freshSessionError(v) {
  if (v == null || v === '') return null
  if (!num(v) || !Number.isInteger(v)) return 'Use a whole number of tokens.'
  if (v < FRESH_MIN || v > FRESH_MAX) return 'Use 50,000 to 900,000 tokens, or leave it empty for off.'
  return null
}

// GET /tasks/{id}/usage: every 5 s while the step runs (and the tab is
// visible), once otherwise. null until the first answer or when it fails.
export function useUsage(taskId, running) {
  const [report, setReport] = useState(null)
  useEffect(() => setReport(null), [taskId])
  useEffect(() => {
    if (!taskId) return undefined
    let live = true
    const load = () =>
      api
        .getUsage(taskId)
        .then((r) => live && setReport(r || null))
        .catch(() => {})
    load()
    const id = running ? setInterval(() => document.visibilityState !== 'hidden' && load(), USAGE_POLL_MS) : null
    return () => {
      live = false
      if (id) clearInterval(id)
    }
  }, [taskId, running])
  return report
}
