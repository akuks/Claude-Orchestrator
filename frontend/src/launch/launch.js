import { revealHidden } from '../guard/guard'
// Launch list helpers (pure). Titles come from people and agents: callers render them as plain text.
export const STALE_MSG = 'changed by someone else — reload'

export const isOpen = (i) => i && (i.status === 'open' || i.status === 'started')

export const bySection = (items, section) => (Array.isArray(items) ? items : []).filter((i) => i.section === section)

// The buttons a row gets. Contributors: Start/Done/Drop. Maintainers also Reopen, Edit, Move, Delete.
export function rowActions(item, canEdit) {
  const out = []
  if (item.status === 'open') out.push('start')
  if (item.status === 'started' || (canEdit && item.status === 'open')) out.push('done')
  if (item.status !== 'dropped') out.push('drop')
  if (canEdit) {
    if (item.status === 'done' || item.status === 'dropped') out.push('reopen')
    out.push('edit', 'move', 'delete')
  }
  return out
}

export const ACTION_STATUS = { start: 'started', done: 'done', drop: 'dropped', reopen: 'open' }

export const nudgeText = (open, wouldBe) =>
  `This looks like new scope: the launch list has ${open} open items; this would become #${wouldBe}.`

// First line of the prompt, trimmed, at most 200 characters.
export function titleFromPrompt(prompt) {
  const line = String(prompt || '').split(/\r\n|[\n\r\u2028\u2029]/).map((l) => l.trim()).find(Boolean) || ''
  return line.slice(0, 200)
}

export const criticalPathsLine = (p) => {
  const paths = Array.isArray(p?.paths) ? p.paths.filter((x) => typeof x === 'string').map((x) => revealHidden(x).text) : []
  return `This run changed ${paths.length} critical path(s): ${paths.join(', ')}`
}

export const questionLaunchLine = (li) => (li && li.number != null ? `blocks launch item #${li.number}: ${li.title}` : null)
