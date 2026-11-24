// Pure helpers for the workflow flow canvas: the boxes a workflow draws, the
// sizes the canvas lays them out with, step edits on the `stages` array, and
// how a run's states map onto box statuses. No React here.
import { stageKind } from '../gates/gates'
import { revealHidden } from '../guard/guard'
import { splitStageTitle } from '../runs/runStages'

// Box sizes (px). Fixed heights keep the connector lines exact without
// measuring the DOM.
export const SIZES = {
  full: { box: 200, h: 64, gap: 12, link: 48, editLink: 72, branch: 56, fixer: 44, tools: 34, chip: 34, caption: 24 },
  mini: { box: 130, h: 30, gap: 6, link: 22, editLink: 22, branch: 0, fixer: 0, tools: 0, chip: 0, caption: 0 },
}
export const sizeOf = (size) => (size === 'mini' ? SIZES.mini : SIZES.full)

export const KIND_WORD = { agent: 'Agents', gate: 'Check', approval: 'Ask a person' }

// The first non-empty line of a text, trimmed.
export function firstLine(text) {
  const s = String(text ?? '')
  const line = s.split('\n').find((l) => l.trim()) || ''
  return line.trim()
}

// The name to show for an agent reference: items name agents by name (older
// ones may hold an id); a check's fixer is always an id.
export function agentName(ref, agents) {
  if (ref == null || ref === '') return ''
  const hit = (agents || []).find((a) => a.id === ref || a.name === ref)
  return revealHidden(hit ? hit.name : String(ref)).text
}

const outputCount = (it) => (Array.isArray(it?.outputs) ? it.outputs.filter((n) => String(n ?? '').trim()).length : 0)

// The drawing model of a workflow: one column per step, each with its boxes.
// item is the agent's index in the step, null for a single-box step, and
// 'fix' for a check's fixer.
export function flowModel(steps, agents) {
  return (Array.isArray(steps) ? steps : []).map((st, index) => {
    const kind = stageKind(st)
    const name = String(st?.name ?? '').trim() || `Step ${index + 1}`
    if (kind === 'agent') {
      const items = Array.isArray(st?.items) ? st.items : []
      const boxes = items.length
        ? items.map((it, k) => ({
            item: k,
            title: agentName(it?.agent, agents) || 'Pick an agent',
            sub: revealHidden(firstLine(it?.prompt)).text,
            retry: st?.auto_retry === true,
            handsOver: outputCount(it),
          }))
        : [{ item: null, title: 'No agent yet', sub: '', retry: false, handsOver: 0 }]
      return { index, kind, name, boxes, fix: null }
    }
    if (kind === 'gate') {
      const run = Array.isArray(st?.run) ? st.run : []
      const cmd = run.find((c) => String(c ?? '').trim()) || ''
      return {
        index,
        kind,
        name,
        boxes: [{ item: null, title: name, sub: String(cmd).trim(), mono: true, retry: !!String(st?.retry ?? '').trim(), handsOver: 0 }],
        fix: st?.fix?.agent ? { title: agentName(st.fix.agent, agents) || 'an agent' } : null,
      }
    }
    return { index, kind, name, boxes: [{ item: null, title: name, sub: firstLine(st?.message), retry: false, handsOver: 0 }], fix: null }
  })
}

// Height of a column of n boxes.
export function columnHeight(n, size) {
  const s = sizeOf(size)
  const k = Math.max(1, n)
  return k * s.h + (k - 1) * s.gap
}

// Vertical centre of each of n boxes, relative to the column's middle.
export function boxOffsets(n, size) {
  const s = sizeOf(size)
  const k = Math.max(1, n)
  return Array.from({ length: k }, (_, i) => (i - (k - 1) / 2) * (s.h + s.gap))
}

// The canvas's inner height (the tallest column) and the extra room above
// (step tools) and below (fixer branch, "+ agent" chip) it needs.
export function layout(model, { size = 'full', mode = 'view' } = {}) {
  const s = sizeOf(size)
  const height = Math.max(s.h, ...model.map((c) => columnHeight(c.boxes.length, size)))
  const half = height / 2
  let above = half
  let below = half
  for (const c of model) {
    const ch = columnHeight(c.boxes.length, size) / 2
    if (mode === 'edit' && size !== 'mini') above = Math.max(above, ch + s.tools)
    if (c.kind === 'agent' && size !== 'mini') above = Math.max(above, ch + s.caption)
    if (c.fix && size !== 'mini') below = Math.max(below, ch + s.branch + s.fixer)
    if (mode === 'edit' && c.kind === 'agent' && size !== 'mini') below = Math.max(below, ch + s.chip)
  }
  return { height, padTop: above - half, padBottom: below - half }
}

// SVG paths of a connector from a column of nLeft boxes to one of nRight
// boxes: lines fan in to the middle and out again, so parallel agents read
// as "these happen together". Returns { lines: [d], arrows: [points] }.
export function connectorPaths(nLeft, nRight, width, height, size) {
  const mid = height / 2
  const cx = width / 2
  const tip = 6
  const lefts = boxOffsets(nLeft, size).map((o) => mid + o)
  const rights = boxOffsets(nRight, size).map((o) => mid + o)
  const lines = []
  if (lefts.length === 1 && rights.length === 1) {
    lines.push(`M0 ${mid} L${width - tip} ${mid}`)
  } else {
    for (const y of lefts) lines.push(y === mid ? `M0 ${mid} L${cx} ${mid}` : `M0 ${y} C${cx * 0.6} ${y} ${cx * 0.6} ${mid} ${cx} ${mid}`)
    for (const y of rights)
      lines.push(y === mid ? `M${cx} ${mid} L${width - tip} ${mid}` : `M${cx} ${mid} C${cx * 1.4} ${mid} ${cx * 1.4} ${y} ${width - tip} ${y}`)
  }
  const arrows = rights.map((y) => `${width - tip},${y - 4} ${width},${y} ${width - tip},${y + 4}`)
  return { lines, arrows }
}

// ---- Step edits -------------------------------------------------------------

export const NEW_STEP_NAME = { agent: 'Agents', gate: 'Check', approval: 'Ask a person' }

// A fresh step of a kind, with what that kind needs (as the type picker seeds).
export function newStep(kind) {
  if (kind === 'gate') return { name: NEW_STEP_NAME.gate, kind: 'gate', run: [''], timeout_min: 60, before: [] }
  if (kind === 'approval') return { name: NEW_STEP_NAME.approval, kind: 'approval', message: '', before: [] }
  return { name: NEW_STEP_NAME.agent, kind: 'agent', items: [{}] }
}

export function insertStep(stages, index, kind) {
  const list = Array.isArray(stages) ? [...stages] : []
  const at = Math.max(0, Math.min(index, list.length))
  list.splice(at, 0, newStep(kind))
  return list
}

// dir: -1 (left) or 1 (right). Out of range leaves the list as it is.
export function moveStep(stages, index, dir) {
  const list = Array.isArray(stages) ? [...stages] : []
  const to = index + dir
  if (index < 0 || index >= list.length || to < 0 || to >= list.length) return list
  const [st] = list.splice(index, 1)
  list.splice(to, 0, st)
  return list
}

export function removeStep(stages, index) {
  return (Array.isArray(stages) ? stages : []).filter((_, i) => i !== index)
}

export function addAgent(stages, index) {
  return (Array.isArray(stages) ? stages : []).map((st, i) => (i === index ? { ...st, items: [...(st.items || []), {}] } : st))
}

// Where the selection goes after a step edit.
export function selAfterMove(sel, from, to) {
  if (!sel) return sel
  if (sel.step === from) return { ...sel, step: to }
  if (sel.step === to) return { ...sel, step: from }
  return sel
}
export function selAfterRemove(sel, index) {
  if (!sel) return sel
  if (sel.step === index) return null
  return sel.step > index ? { ...sel, step: sel.step - 1 } : sel
}
export function selAfterInsert(sel, index) {
  if (!sel) return sel
  return sel.step >= index ? { ...sel, step: sel.step + 1 } : sel
}

// ---- Selection and errors ---------------------------------------------------

export const boxKey = (step, item) => `${step}:${item == null ? '' : item}`

// The box a form field belongs to: ['stages', 2, 'items', 1, 'prompt'] → step
// 2, agent 1; ['stages', 1, 'fix', 'agent'] → step 1's fixer. Other step
// fields of an agents step go to its first agent. null outside the steps.
export function fieldSelection(namePath, stages) {
  const p = Array.isArray(namePath) ? namePath : [namePath]
  if (p[0] !== 'stages' || typeof p[1] !== 'number') return null
  const step = p[1]
  if (p[2] === 'items' && typeof p[3] === 'number') return { step, item: p[3] }
  if (p[2] === 'fix') return { step, item: 'fix' }
  const kind = stageKind((stages || [])[step])
  return { step, item: kind === 'agent' ? 0 : null }
}

// Keys of every box with an error, for the red dots.
export function errorBoxes(errorFields, stages) {
  const out = new Set()
  for (const f of errorFields || []) {
    if (!f?.errors?.length) continue
    const s = fieldSelection(f.name, stages)
    if (s) out.add(boxKey(s.step, s.item))
  }
  return out
}

// ---- Run statuses -----------------------------------------------------------

export const STATUSES = ['done', 'running', 'waiting', 'failed', 'held', 'next']
export const STATUS_WORD = { done: 'done', running: 'running', waiting: 'waiting', failed: 'failed', held: 'on hold', next: 'not started' }

const STATUS_MAP = {
  done: 'done',
  passed: 'done',
  approved: 'done',
  completed: 'done',
  running: 'running',
  current: 'running',
  queued: 'running',
  waiting_approval: 'waiting',
  needs_approval: 'waiting',
  awaiting_approval: 'waiting',
  budget: 'waiting',
  held: 'held',
  failed: 'failed',
  rejected: 'failed',
  cancelled: 'failed',
}

// Any step, check or task state → one of STATUSES.
export const flowStatus = (raw) => STATUS_MAP[raw] || 'next'

// Statuses a check or person step's own state carries; others ('waiting' = not
// started) defer to the run position.
const OWN_STATE = new Set(['passed', 'approved', 'failed', 'rejected', 'waiting_approval', 'needs_approval', 'budget', 'held', 'running'])

const byCreated = (a, b) => String(a.created_at || '').localeCompare(String(b.created_at || ''))

// Per step of a run: { status, items: [status], itemTasks: [taskId|null],
// fix: status|null, fixTask: taskId|null }.
// stages: run.stages; states: runStages() output; sstate: the run's
// stage_state; tasks: the run's tasks; agents: to match items to tasks.
export function runBoxStatuses({ stages, states, sstate, tasks, agents } = {}) {
  const all = Array.isArray(tasks) ? tasks : []
  return (Array.isArray(stages) ? stages : []).map((st, i) => {
    const kind = stageKind(st)
    const own = sstate?.[String(i)] || {}
    const attempt = Number(own.attempt) || 0
    const mine = all.filter((t) => t?.stage_index === i && (t.stage_attempt || 0) === attempt).sort(byCreated)
    const base = flowStatus(states?.[i]?.state)
    if (kind === 'agent') {
      const status = own.status === 'budget' || own.status === 'held' ? flowStatus(own.status) : base
      const items = Array.isArray(st?.items) && st.items.length ? st.items : [null]
      const used = new Set()
      const itemTasks = items.map((it, k) => {
        const name = it ? agentName(it.agent, agents) : ''
        let hit = name ? mine.find((t) => !used.has(t.id) && splitStageTitle(t.title).agent === name) : null
        if (!hit && mine[k] && !used.has(mine[k].id)) hit = mine[k]
        if (hit) used.add(hit.id)
        return hit ? hit.id : null
      })
      const itemStatus = itemTasks.map((id) => {
        const t = id && mine.find((x) => x.id === id)
        return t ? flowStatus(t.status) : status
      })
      return { status, items: itemStatus, itemTasks, fix: null, fixTask: null }
    }
    let status = OWN_STATE.has(own.status) ? flowStatus(own.status) : base
    let fix = null
    let fixTask = null
    if (kind === 'gate' && st?.fix?.agent) {
      const last = mine[mine.length - 1] || null
      fixTask = own.fix_task || last?.id || null
      if (own.status === 'fixing') {
        status = 'failed'
        fix = 'running'
      } else fix = last ? flowStatus(last.status) : 'next'
    } else if (own.status === 'fixing') status = 'failed'
    return { status, items: [status], itemTasks: [null], fix, fixTask }
  })
}
