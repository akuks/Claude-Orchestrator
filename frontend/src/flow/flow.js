// Pure helpers for the workflow flow canvas: the boxes a workflow draws, the
// sizes the canvas lays them out with, step edits on the `stages` array, and
// how a run's states map onto box statuses. No React here.
import { stageKind } from '../gates/gates'
import { revealHidden } from '../guard/guard'
import { splitStageTitle } from '../runs/runStages'

// Box sizes (px). Fixed heights keep the connector lines exact without
// measuring the DOM.
export const SIZES = {
  full: { box: 200, h: 64, gap: 12, link: 40, editLink: 72, branch: 56, fixer: 44, tools: 34, chip: 34, caption: 24 },
  mini: { box: 130, h: 30, gap: 6, link: 26, editLink: 26, branch: 0, fixer: 0, tools: 0, chip: 0, caption: 0 },
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

const outputNames = (it) => (Array.isArray(it?.outputs) ? it.outputs.map((n) => String(n ?? '').trim()).filter(Boolean) : [])

// What a box's badges mean, in words (tooltips and screen readers).
export function badgeWords(kind, box) {
  const out = []
  if (box?.retry) out.push(kind === 'gate' ? 'Re-runs only the failures first' : 'Tries once more if it fails')
  const names = box?.outputs || []
  if (names.length) out.push(`Hands over ${names.length} thing${names.length === 1 ? '' : 's'}: ${names.join(', ')}`)
  return out
}

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
            handsOver: outputNames(it).length,
            outputs: outputNames(it).map((n) => revealHidden(n).text),
          }))
        : [{ item: null, title: 'No agent yet', sub: '', retry: false, handsOver: 0 }]
      return { index, kind, name, boxes, fix: null, deps: stepDeps(steps, index) }
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
        deps: stepDeps(steps, index),
      }
    }
    return { index, kind, name, boxes: [{ item: null, title: name, sub: firstLine(st?.message), retry: false, handsOver: 0 }], fix: null, deps: stepDeps(steps, index) }
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

// What a step's column needs above and below its boxes (step tools, caption,
// fixer branch, "+ agent" chip): they hang outside the column.
function extras(col, size, mode) {
  const s = sizeOf(size)
  if (size === 'mini') return { above: 0, below: 0 }
  const edit = mode === 'edit'
  return {
    above: Math.max(edit ? s.tools : 0, col.kind === 'agent' ? s.caption : 0),
    below: Math.max(col.fix ? s.branch + s.fixer : 0, edit && col.kind === 'agent' ? s.chip : 0),
  }
}

// The same for a step stacked with others: a run's usage line needs room too.
export function stackExtras(col, size, mode) {
  const ex = extras(col, size, mode)
  return mode === 'run' && size !== 'mini' ? { above: ex.above, below: ex.below + 22 } : ex
}

// A step's slot in a stack of several steps: its column plus what hangs off it.
const slotHeight = (col, size, mode) => {
  const ex = stackExtras(col, size, mode)
  return ex.above + columnHeight(col.boxes.length, size) + ex.below
}
const stackHeight = (cols, size, mode) => cols.reduce((a, c) => a + slotHeight(c, size, mode), 0) + (cols.length - 1) * sizeOf(size).gap

// The canvas's inner height (the tallest column or stack of steps) and the
// extra room above (step tools) and below (fixer branch, "+ agent" chip) it
// needs. groups: step indexes sharing a column (default: one step each).
export function layout(model, { size = 'full', mode = 'view', groups = null } = {}) {
  const s = sizeOf(size)
  const gs = groups || model.map((_, i) => [i])
  const parts = gs.map((g) => {
    if (g.length === 1) {
      const c = model[g[0]]
      const ch = columnHeight(c.boxes.length, size) / 2
      const ex = extras(c, size, mode)
      return { h: ch * 2, up: ch + ex.above, down: ch + ex.below }
    }
    const h = stackHeight(g.map((i) => model[i]), size, mode)
    return { h, up: h / 2, down: h / 2 }
  })
  const height = Math.max(s.h, ...parts.map((p) => p.h))
  const half = height / 2
  const above = Math.max(half, ...parts.map((p) => p.up))
  const below = Math.max(half, ...parts.map((p) => p.down))
  return { height, padTop: above - half, padBottom: below - half }
}

// ---- Which steps wait for which ---------------------------------------------

// The earlier steps step i waits for. `after` missing = the step before.
export function stepDeps(steps, i) {
  const a = (Array.isArray(steps) ? steps : [])[i]?.after
  if (!Array.isArray(a)) return i > 0 ? [i - 1] : []
  return [...new Set(a.filter((j) => Number.isInteger(j) && j >= 0 && j < i))].sort((x, y) => x - y)
}

// True when any step says what it waits for (otherwise the workflow is a plain line).
export const usesAfter = (steps) => (Array.isArray(steps) ? steps : []).some((st) => Array.isArray(st?.after))

// How many waits deep each step is (a step that starts at once is 0).
export function stepDepths(steps) {
  const out = []
  ;(Array.isArray(steps) ? steps : []).forEach((_, i) => {
    const d = stepDeps(steps, i)
    out.push(d.length ? 1 + Math.max(...d.map((j) => out[j])) : 0)
  })
  return out
}

// Steps in columns by depth; steps of one column are stacked in step order.
export function stepGroups(steps) {
  const depths = stepDepths(steps)
  const groups = []
  depths.forEach((d, i) => {
    ;(groups[d] ||= []).push(i)
  })
  return groups
}

// Steps nobody waits for: they lead to Done.
export function stepSinks(steps) {
  const list = Array.isArray(steps) ? steps : []
  const waited = new Set()
  list.forEach((_, i) => stepDeps(list, i).forEach((j) => waited.add(j)))
  return list.map((_, i) => i).filter((i) => !waited.has(i))
}

// The lines of the picture: { from: 'start' | step, to: step | 'end' }.
export function flowEdges(steps) {
  const list = Array.isArray(steps) ? steps : []
  if (!list.length) return [{ from: 'start', to: 'end' }]
  const out = []
  list.forEach((_, i) => {
    const d = stepDeps(list, i)
    if (!d.length) out.push({ from: 'start', to: i })
    d.forEach((j) => out.push({ from: j, to: i }))
  })
  stepSinks(list).forEach((i) => out.push({ from: i, to: 'end' }))
  return out
}

// A step's name for sentences ("Step 3" when it has none).
export const stepLabel = (steps, i) => String((Array.isArray(steps) ? steps : [])[i]?.name ?? '').trim() || `Step ${i + 1}`

// Vertical middle of each step's column and of each of its boxes, in the
// canvas's own coordinates (0 .. layout height).
export function stepYs(model, groups, height, { size = 'full', mode = 'view' } = {}) {
  const s = sizeOf(size)
  const out = []
  for (const g of groups) {
    if (g.length === 1) {
      const i = g[0]
      out[i] = { center: height / 2, boxes: boxOffsets(model[i].boxes.length, size).map((o) => height / 2 + o) }
      continue
    }
    let y = (height - stackHeight(g.map((i) => model[i]), size, mode)) / 2
    for (const i of g) {
      const c = model[i]
      const ex = stackExtras(c, size, mode)
      const center = y + ex.above + columnHeight(c.boxes.length, size) / 2
      out[i] = { center, boxes: boxOffsets(c.boxes.length, size).map((o) => center + o) }
      y += slotHeight(c, size, mode) + s.gap
    }
  }
  return out
}

// Everything the canvas needs to draw its lines.
export function flowPlan(steps, model, { size = 'full', mode = 'view' } = {}) {
  const groups = stepGroups(steps)
  const geo = layout(model, { size, mode, groups })
  const ys = stepYs(model, groups, geo.height, { size, mode })
  const groupOf = []
  groups.forEach((g, k) => g.forEach((i) => (groupOf[i] = k)))
  const edges = flowEdges(steps)
  const gof = (n) => (n === 'start' ? -1 : n === 'end' ? groups.length : groupOf[n])
  const lanes = edges.filter((e) => gof(e.to) - gof(e.from) > 1).length
  // Lines that skip a column run along a lane above everything, inside the canvas.
  const unit = size === 'mini' ? 3 : 6
  const laneRoom = lanes ? unit + unit * lanes : 0
  return { groups, groupOf, geo, ys, edges, mid: geo.height / 2, count: groups.length, lanes, unit, laneRoom, top: lanes ? geo.padTop + laneRoom : 0 }
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

const TIP = 6

// The lines of one edge inside one connector. src/dst: { center, boxes }.
function directShape(src, dst, width, size) {
  if (Math.abs(src.center - dst.center) < 0.01) return connectorPaths(src.boxes.length, dst.boxes.length, width, src.center * 2, size)
  const cx = width / 2
  const arrows = dst.boxes.map((y) => `${width - TIP},${y - 4} ${width},${y} ${width - TIP},${y + 4}`)
  if (src.boxes.length === 1 && dst.boxes.length === 1) {
    return { lines: [`M0 ${src.center} C${cx} ${src.center} ${cx} ${dst.center} ${width - TIP} ${dst.center}`], arrows }
  }
  const ym = (src.center + dst.center) / 2
  const lines = [
    ...src.boxes.map((y) => `M0 ${y} C${cx * 0.6} ${y} ${cx * 0.6} ${ym} ${cx} ${ym}`),
    ...dst.boxes.map((y) => `M${cx} ${ym} C${cx * 1.4} ${ym} ${cx * 1.4} ${y} ${width - TIP} ${y}`),
  ]
  return { lines, arrows }
}

// The lines inside connector k (the gap before column k; the last one leads to
// Done). Each: { from, to, kind: 'direct' | 'exit' | 'pass' | 'enter', lines,
// arrows, y } — a line that skips columns rides along the top of the canvas.
export function connectorShapes(plan, k, width, size = 'full') {
  const { groupOf, ys, edges, mid, count } = plan
  const at = (n) => (n === 'start' || n === 'end' ? { center: mid, boxes: [mid] } : ys[n])
  const col = (n) => (n === 'start' ? -1 : n === 'end' ? count : groupOf[n])
  let lane = 0
  const out = []
  const cx = width / 2
  // Rounded corners: down/up along x = cx between the lane and a box line.
  const corner = (dy) => Math.max(1, Math.min(8, cx - 2, Math.abs(dy) - 1))
  for (const e of edges) {
    const gf = col(e.from)
    const gt = col(e.to)
    const skip = gt - gf > 1
    const my = skip ? -(plan.geo.padTop + plan.unit + plan.unit * lane++) : 0
    if (k < gf + 1 || k > gt) continue
    const src = at(e.from)
    const dst = at(e.to)
    if (!skip) {
      out.push({ ...e, kind: 'direct', ...directShape(src, dst, width, size), y: (src.center + dst.center) / 2 })
    } else if (k === gf + 1) {
      out.push({
        ...e,
        kind: 'exit',
        lines: src.boxes.map((y) => {
          const r = corner(y - my)
          return `M0 ${y} L${cx - r} ${y} Q${cx} ${y} ${cx} ${y - r} L${cx} ${my + r} Q${cx} ${my} ${cx + r} ${my} L${width} ${my}`
        }),
        arrows: [],
        y: my,
      })
    } else if (k === gt) {
      out.push({
        ...e,
        kind: 'enter',
        lines: dst.boxes.map((y) => {
          const r = corner(y - my)
          return `M0 ${my} L${cx - r} ${my} Q${cx} ${my} ${cx} ${my + r} L${cx} ${y - r} Q${cx} ${y} ${cx + r} ${y} L${width - TIP} ${y}`
        }),
        arrows: dst.boxes.map((y) => `${width - TIP},${y - 4} ${width},${y} ${width - TIP},${y + 4}`),
        y: my,
      })
    } else out.push({ ...e, kind: 'pass', lines: [`M0 ${my} L${width} ${my}`], arrows: [], y: my })
  }
  return out
}

// ---- Step edits -------------------------------------------------------------

export const NEW_STEP_NAME = { agent: 'Agents', gate: 'Check', approval: 'Ask a person' }

// A fresh step of a kind, with what that kind needs (as the type picker seeds).
export function newStep(kind) {
  if (kind === 'gate') return { name: NEW_STEP_NAME.gate, kind: 'gate', run: [''], timeout_min: 60, before: [] }
  if (kind === 'approval') return { name: NEW_STEP_NAME.approval, kind: 'approval', message: '', before: [] }
  return { name: NEW_STEP_NAME.agent, kind: 'agent', items: [{}] }
}

// Every step with its waits written out (so they can be re-numbered).
const explicit = (list) => list.map((st, i) => ({ ...st, after: stepDeps(list, i) }))
const uniqSorted = (a) => [...new Set(a)].sort((x, y) => x - y)

// A step put in at `index`, in sequence: in a workflow that says what steps wait
// for, it waits for what the step it lands before waited for, and that step now
// waits for it (at the end it waits for every last step). A plain line stays plain.
export function insertStep(stages, index, kind) {
  const list = Array.isArray(stages) ? [...stages] : []
  const at = Math.max(0, Math.min(index, list.length))
  if (!usesAfter(list)) {
    list.splice(at, 0, newStep(kind))
    return list
  }
  const old = explicit(list)
  // Between two steps that run side by side there is no "in sequence": leave the list
  // as it is. (The canvas only offers "+" on a single line or at the end, so the UI
  // never asks for this.)
  if (at > 0 && at < old.length && !old[at].after.includes(at - 1)) return list
  const next = old.map((st) => ({ ...st, after: st.after.map((j) => (j >= at ? j + 1 : j)) }))
  const waits = at < old.length ? old[at].after : stepSinks(old)
  next.splice(at, 0, { ...newStep(kind), after: [...waits] })
  if (at + 1 < next.length) next[at + 1] = { ...next[at + 1], after: [at] }
  return next
}

// Why a step can't swap places with its neighbour, in words (null: it can).
// Only workflows that say what steps wait for have the rule; a plain line moves freely.
export function moveBlocked(stages, index, dir) {
  const list = Array.isArray(stages) ? stages : []
  const to = index + dir
  if (!usesAfter(list) || index < 0 || index >= list.length || to < 0 || to >= list.length) return null
  const lo = Math.min(index, to)
  const hi = Math.max(index, to)
  if (!stepDeps(list, hi).includes(lo)) return null
  return dir < 0
    ? `“${stepLabel(list, index)}” waits for “${stepLabel(list, to)}”, so it can't go above it.`
    : `“${stepLabel(list, to)}” waits for “${stepLabel(list, index)}”, so “${stepLabel(list, index)}” can't go below it.`
}

// dir: -1 (left) or 1 (right). Out of range, or past a step it waits for, leaves
// the list as it is; waits are re-numbered.
export function moveStep(stages, index, dir) {
  const list = Array.isArray(stages) ? [...stages] : []
  const to = index + dir
  if (index < 0 || index >= list.length || to < 0 || to >= list.length) return list
  if (moveBlocked(list, index, dir)) return list
  if (!usesAfter(list)) {
    const [st] = list.splice(index, 1)
    list.splice(to, 0, st)
    return list
  }
  const swap = (j) => (j === index ? to : j === to ? index : j)
  const next = explicit(list).map((st) => ({ ...st, after: uniqSorted(st.after.map(swap)) }))
  ;[next[index], next[to]] = [next[to], next[index]]
  return next
}

// Takes a step out. Steps that waited for it wait for what it waited for instead. A plain line stays plain.
export function removeStep(stages, index) {
  const list = Array.isArray(stages) ? stages : []
  if (!usesAfter(list)) return list.filter((_, i) => i !== index)
  const old = explicit(list)
  const gone = old[index]?.after || []
  return old
    .filter((_, i) => i !== index)
    .map((st) => {
      let a = st.after
      // It inherits what the deleted step waited for, so it never starts earlier than before.
      if (a.includes(index)) a = [...a.filter((j) => j !== index), ...gone]
      return { ...st, after: uniqSorted(a.map((j) => (j > index ? j - 1 : j))) }
    })
}

// A plain sentence about what deleting a step did to the steps waiting for it
// (null when nothing waited only for it).
export function removeNote(stages, index) {
  const list = Array.isArray(stages) ? stages : []
  if (!usesAfter(list)) return null
  const gone = stepDeps(list, index)
  const named = list
    .map((_, i) => i)
    .filter((i) => i > index && stepDeps(list, i).length === 1 && stepDeps(list, i)[0] === index)
    .map((i) => `“${stepLabel(list, i)}”`)
  if (!named.length) return null
  const many = named.length > 1
  if (!gone.length) return `${named.join(', ')} now ${many ? 'start' : 'starts'} at once.`
  return `${named.join(', ')} now ${many ? 'wait' : 'waits'} for what “${stepLabel(list, index)}” waited for.`
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

// "under a minute", "4 min", "2 h 5 min" (seconds in, plain words out).
export function plainDuration(seconds) {
  const s = seconds == null ? NaN : Number(seconds)
  if (!Number.isFinite(s) || s < 0) return null
  const m = Math.round(s / 60)
  if (s < 60 || m < 1) return 'under a minute'
  if (m < 60) return `${m} min`
  const h = Math.floor(m / 60)
  if (h >= 24) {
    const d = Math.floor(h / 24)
    const rh = h % 24
    return rh ? `${d} d ${rh} h` : `${d} d`
  }
  const r = m % 60
  return r ? `${h} h ${r} min` : `${h} h`
}

// The small line on a run box: "4 min · $0.12". A step not started (null) has
// none; a cost that rounds to $0.00 is left out.
export function usageLine(usage) {
  if (!usage || typeof usage !== 'object') return null
  const time = plainDuration(usage.seconds)
  const cost = Number(usage.cost_usd) >= 0.005 ? `$${Number(usage.cost_usd).toFixed(2)}` : null
  return [time, cost].filter(Boolean).join(' · ') || null
}
