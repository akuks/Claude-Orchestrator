// Questions inbox helpers (pure). Every text on a question comes from an agent (or its last reply), so it is
// shown as plain text with hidden characters revealed, as the approval card does.
import { revealHidden } from '../guard/guard'

// Open questions the viewer may answer: the Rail badge count.
export function answerableCount(list) {
  return (Array.isArray(list) ? list : []).filter((q) => q && q.status === 'open' && q.can_answer === true).length
}

// Only the open ones, in the server's order (newest first).
export function openQuestions(list) {
  return (Array.isArray(list) ? list : []).filter((q) => q && q.status === 'open')
}

const SOURCE = {
  agent: 'asked by the agent',
  final_text: "from the agent's last reply",
  person: 'asked by a person',
}
export const sourceLabel = (s) => SOURCE[s] || 'asked'

export function reversibleChip(r) {
  if (r === true) return { text: 'reversible', warn: false }
  if (r === false) return { text: 'not reversible', warn: true }
  return { text: 'reversible: unknown', warn: false }
}

// What the card shows: every text revealed, options marked when one is the recommendation, and how many
// hidden characters were found in total.
export function questionView(q) {
  const r = (s) => revealHidden(s || '')
  const question = r(q?.question)
  const rec = q?.recommendation ? r(q.recommendation) : null
  const blocks = q?.blocks ? r(q.blocks) : null
  const answer = q?.answer ? r(q.answer) : null
  const options = (Array.isArray(q?.options) ? q.options : []).map((o) => {
    const v = r(o)
    return { value: o, text: v.text, count: v.count, recommended: rec != null && o === q.recommendation }
  })
  const hidden =
    question.count + (rec?.count || 0) + (blocks?.count || 0) + options.reduce((n, o) => n + o.count, 0)
  return {
    question: question.text,
    recommendation: rec?.text ?? null,
    recInOptions: options.some((o) => o.recommended),
    blocks: blocks?.text ?? null,
    answer: answer?.text ?? null,
    options,
    hidden,
    reversible: reversibleChip(q?.reversible),
    source: sourceLabel(q?.source),
  }
}
