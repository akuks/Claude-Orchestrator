import { budgetChip, contextChip } from './usage'
import './usage.css'

const n = (v) => Number(v).toLocaleString('en-US')

// The step's context and budget chips (task header).
export default function UsageChips({ report }) {
  const step = report?.step
  const ctx = contextChip(step)
  const budget = budgetChip(step)
  if (!ctx && !budget) return null
  const ctxTitle =
    ctx &&
    `Conversation size now: ${n(step.context_tokens)} tokens` +
      (step.peak_context != null ? ` (peak ${n(step.peak_context)})` : '') +
      (step.fresh_session_tokens ? `; fresh session at ${n(step.fresh_session_tokens)}` : '; no fresh-session threshold')
  const budgetTitle =
    budget && `$${Number(step.est_cost_usd).toFixed(2)} of the step's $${Number(step.max_budget_usd).toFixed(2)} budget`
  return (
    <span className="co-usage-chips" data-testid="usage-chips">
      {ctx && (
        <span className={`co-usage-chip co-usage-chip-${ctx.level}`} data-testid="usage-context" title={ctxTitle}>
          {ctx.text}
        </span>
      )}
      {budget && (
        <span className={`co-usage-chip co-usage-chip-${budget.level}`} data-testid="usage-budget" title={budgetTitle}>
          {budget.text}
        </span>
      )}
    </span>
  )
}
