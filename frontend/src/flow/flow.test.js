import { describe, expect, it } from 'vitest'
import {
  addAgent,
  plainDuration,
  usageLine,
  boxKey,
  boxOffsets,
  columnHeight,
  connectorPaths,
  connectorShapes,
  flowEdges,
  flowPlan,
  moveBlocked,
  removeNote,
  stepDeps,
  stepDepths,
  stepGroups,
  errorBoxes,
  fieldSelection,
  flowModel,
  flowStatus,
  insertStep,
  layout,
  moveStep,
  newStep,
  removeStep,
  runBoxStatuses,
  selAfterInsert,
  selAfterMove,
  selAfterRemove,
} from './flow'

const STEPS = [
  { name: 'Build', auto_retry: true, items: [{ agent: 'Planner', prompt: '\n  Plan it {{input}}\nmore' }, { agent: 'ag2', prompt: 'b', outputs: ['plan_path', 'summary'] }] },
  { name: 'Tests', kind: 'gate', run: ['', 'npm test'], retry: 'npm test -- --lf', fix: { agent: 'ag1', rounds: 2 } },
  { name: 'Sign', kind: 'approval', message: 'Look at it\nplease' },
]
const AGENTS = [
  { id: 'ag1', name: 'Fixer' },
  { id: 'ag2', name: 'Builder' },
]

describe('flowModel', () => {
  it('draws one column per step and one box per parallel agent', () => {
    const m = flowModel(STEPS, AGENTS)
    expect(m.map((c) => c.kind)).toEqual(['agent', 'gate', 'approval'])
    expect(m[0].boxes.map((b) => b.title)).toEqual(['Planner', 'Builder'])
    expect(m[0].boxes[0].sub).toBe('Plan it {{input}}')
    expect(m[0].boxes.map((b) => [b.retry, b.handsOver])).toEqual([[true, 0], [true, 2]])
    expect(m[1].boxes[0]).toMatchObject({ title: 'Tests', sub: 'npm test', mono: true, retry: true })
    expect(m[1].fix).toEqual({ title: 'Fixer' })
    expect(m[2].boxes[0]).toMatchObject({ title: 'Sign', sub: 'Look at it' })
  })
  it('names unnamed steps, empty agent steps and reveals hidden characters', () => {
    const m = flowModel([{ items: [] }, { name: ' ', kind: 'gate' }, { items: [{ agent: 'a​b' }] }], [])
    expect(m[0].name).toBe('Step 1')
    expect(m[0].boxes).toEqual([{ item: null, title: 'No agent yet', sub: '', retry: false, handsOver: 0 }])
    expect(m[1].boxes[0].title).toBe('Step 2')
    expect(m[1].fix).toBeNull()
    expect(m[2].boxes[0].title).toBe('a⟦U+200B⟧b')
  })
})

describe('layout', () => {
  it('sizes columns and centres boxes', () => {
    expect(columnHeight(1)).toBe(64)
    expect(columnHeight(3)).toBe(3 * 64 + 2 * 12)
    expect(boxOffsets(2)).toEqual([-38, 38])
    expect(boxOffsets(1, 'mini')).toEqual([0])
  })
  it('reserves room for the fixer branch, the step tools and the agent chip', () => {
    const m = flowModel(STEPS, AGENTS)
    const v = layout(m, { mode: 'view' })
    expect(v.height).toBe(columnHeight(2))
    expect(v.padBottom).toBe(32 + 56 + 44 - columnHeight(2) / 2)
    const e = layout(m, { mode: 'edit' })
    expect(e.padTop).toBeGreaterThan(v.padTop)
    expect(layout(flowModel([{ items: [{}] }], []), { mode: 'view' })).toEqual({ height: 64, padTop: 24, padBottom: 0 })
    expect(layout(m, { size: 'mini' }).padBottom).toBe(0)
  })
  it('a connector fans out to parallel boxes and back in', () => {
    const one = connectorPaths(1, 1, 48, 64)
    expect(one.lines).toHaveLength(1)
    expect(one.arrows).toHaveLength(1)
    const fan = connectorPaths(1, 3, 48, columnHeight(3))
    expect(fan.lines).toHaveLength(4)
    expect(fan.arrows).toHaveLength(3)
  })
})

describe('step edits', () => {
  const base = [{ name: 'A' }, { name: 'B' }, { name: 'C' }]
  it('newStep seeds what each kind needs', () => {
    expect(newStep('agent')).toEqual({ name: 'Agents', kind: 'agent', items: [{}] })
    expect(newStep('gate')).toEqual({ name: 'Check', kind: 'gate', run: [''], timeout_min: 60, before: [] })
    expect(newStep('approval')).toEqual({ name: 'Ask a person', kind: 'approval', message: '', before: [] })
  })
  it('insertStep puts the new step at the index, including both ends', () => {
    expect(insertStep(base, 0, 'gate').map((s) => s.name)).toEqual(['Check', 'A', 'B', 'C'])
    expect(insertStep(base, 2, 'approval').map((s) => s.name)).toEqual(['A', 'B', 'Ask a person', 'C'])
    expect(insertStep(base, 3, 'agent').map((s) => s.name)).toEqual(['A', 'B', 'C', 'Agents'])
    expect(insertStep([], 0, 'agent')).toHaveLength(1)
    expect(base.map((s) => s.name)).toEqual(['A', 'B', 'C'])
  })
  it('moveStep swaps with a neighbour and ignores moves off the ends', () => {
    expect(moveStep(base, 0, 1).map((s) => s.name)).toEqual(['B', 'A', 'C'])
    expect(moveStep(base, 2, -1).map((s) => s.name)).toEqual(['A', 'C', 'B'])
    expect(moveStep(base, 0, -1).map((s) => s.name)).toEqual(['A', 'B', 'C'])
    expect(moveStep(base, 2, 1).map((s) => s.name)).toEqual(['A', 'B', 'C'])
  })
  it('removeStep and addAgent', () => {
    expect(removeStep(base, 1).map((s) => s.name)).toEqual(['A', 'C'])
    expect(addAgent([{ items: [{ agent: 'x' }] }], 0)[0].items).toEqual([{ agent: 'x' }, {}])
  })
  it('the selection follows the edits', () => {
    expect(selAfterMove({ step: 0, item: 1 }, 0, 1)).toEqual({ step: 1, item: 1 })
    expect(selAfterMove({ step: 1 }, 0, 1)).toEqual({ step: 0 })
    expect(selAfterRemove({ step: 1 }, 1)).toBeNull()
    expect(selAfterRemove({ step: 2 }, 1)).toEqual({ step: 1 })
    expect(selAfterInsert({ step: 1 }, 1)).toEqual({ step: 2 })
    expect(selAfterInsert({ step: 0 }, 1)).toEqual({ step: 0 })
  })
})

describe('errors map to boxes', () => {
  it('fieldSelection', () => {
    expect(fieldSelection(['stages', 0, 'items', 1, 'prompt'], STEPS)).toEqual({ step: 0, item: 1 })
    expect(fieldSelection(['stages', 0, 'name'], STEPS)).toEqual({ step: 0, item: 0 })
    expect(fieldSelection(['stages', 1, 'fix', 'agent'], STEPS)).toEqual({ step: 1, item: 'fix' })
    expect(fieldSelection(['stages', 1, 'run', 0], STEPS)).toEqual({ step: 1, item: null })
    expect(fieldSelection(['name'], STEPS)).toBeNull()
  })
  it('errorBoxes', () => {
    const keys = errorBoxes(
      [
        { name: ['stages', 1, 'run', 0], errors: ['bad'] },
        { name: ['stages', 0, 'items', 1, 'agent'], errors: ['Pick an agent'] },
        { name: ['stages', 2, 'message'], errors: [] },
        { name: ['name'], errors: ['x'] },
      ],
      STEPS,
    )
    expect([...keys].sort()).toEqual([boxKey(0, 1), boxKey(1, null)].sort())
  })
})

describe('run statuses', () => {
  it('flowStatus maps every state', () => {
    for (const s of ['done', 'passed', 'approved', 'completed']) expect(flowStatus(s)).toBe('done')
    for (const s of ['running', 'current', 'queued']) expect(flowStatus(s)).toBe('running')
    for (const s of ['waiting_approval', 'needs_approval', 'budget', 'awaiting_approval']) expect(flowStatus(s)).toBe('waiting')
    expect(flowStatus('held')).toBe('held')
    for (const s of ['failed', 'rejected', 'cancelled']) expect(flowStatus(s)).toBe('failed')
    for (const s of ['next', 'waiting', undefined, 'odd']) expect(flowStatus(s)).toBe('next')
  })
  it('each agent takes its own task status; a check being fixed shows failed with a running fixer', () => {
    const stages = [
      { name: 'Dev', items: [{ agent: 'A' }, { agent: 'B' }] },
      { name: 'Tests', kind: 'gate', run: ['x'], fix: { agent: 'ag1' } },
      { name: 'Sign', kind: 'approval' },
    ]
    const tasks = [
      { id: 'tb', stage_index: 0, status: 'running', title: '[Dev] B: go', created_at: '2', stage_attempt: 1 },
      { id: 'ta', stage_index: 0, status: 'completed', title: '[Dev] A: go', created_at: '1', stage_attempt: 1 },
      { id: 'old', stage_index: 0, status: 'failed', title: '[Dev] A: go', stage_attempt: 0 },
      { id: 'fx', stage_index: 1, status: 'running', title: '[Tests] Fixer: fix round 1', stage_attempt: 1 },
    ]
    const sstate = { 0: { attempt: 1 }, 1: { status: 'fixing', attempt: 1, fix_task: 'fx' }, 2: { status: 'waiting' } }
    const out = runBoxStatuses({ stages, states: [{ state: 'current' }, { state: 'current' }, { state: 'next' }], sstate, tasks })
    expect(out[0].status).toBe('running')
    expect(out[0].itemTasks).toEqual(['ta', 'tb'])
    expect(out[0].items).toEqual(['done', 'running'])
    expect(out[1]).toMatchObject({ status: 'failed', fix: 'running', fixTask: 'fx' })
    expect(out[2].status).toBe('next')
  })
  it('a held or budget-held agents step, and a waiting approval', () => {
    const out = runBoxStatuses({
      stages: [{ items: [{ agent: 'A' }] }, { kind: 'approval' }, { kind: 'gate', run: ['x'] }],
      states: [{ state: 'current' }, { state: 'waiting_approval' }, { state: 'done' }],
      sstate: { 0: { status: 'budget' }, 1: { status: 'waiting_approval' }, 2: { status: 'passed' } },
      tasks: [],
    })
    expect(out.map((o) => o.status)).toEqual(['waiting', 'waiting', 'done'])
    expect(out[0].items).toEqual(['waiting'])
    expect(out[2].fix).toBeNull()
  })
})

describe('usageLine / plainDuration', () => {
  it('uses short plain forms', () => {
    expect(plainDuration(20)).toBe('under a minute')
    expect(plainDuration(250)).toBe('4 min')
    expect(plainDuration(3600)).toBe('1 h')
    expect(plainDuration(7500)).toBe('2 h 5 min')
    expect(plainDuration(null)).toBeNull()
    expect(plainDuration(26 * 3600)).toBe('1 d 2 h')
    expect(plainDuration(48 * 3600)).toBe('2 d')
  })
  it('joins time and cost, hides a zero cost, and nothing for a step not started', () => {
    expect(usageLine({ seconds: 250, cost_usd: 0.123 })).toBe('4 min · $0.12')
    expect(usageLine({ seconds: 250, cost_usd: 0 })).toBe('4 min')
    expect(usageLine({ seconds: 0, cost_usd: 0 })).toBe('under a minute')
    expect(usageLine(null)).toBeNull()
  })
})

describe('steps side by side: layout', () => {
  const LINE = STEPS
  // A (0) → B (1) and C (2) together → D (3) waits for both
  const DIAMOND = [
    { name: 'A', kind: 'approval', message: 'm' },
    { name: 'B', kind: 'approval', message: 'm', after: [0] },
    { name: 'C', kind: 'approval', message: 'm', after: [0] },
    { name: 'D', kind: 'approval', message: 'm', after: [1, 2] },
  ]
  it('a missing `after` means the step before; [] starts at once', () => {
    expect([0, 1, 2].map((i) => stepDeps(LINE, i))).toEqual([[], [0], [1]])
    expect(stepDeps([{}, { after: [] }, { after: [1, 0, 0, 5, 2] }], 2)).toEqual([0, 1])
    expect(stepDeps([{}, { after: [] }], 1)).toEqual([])
  })
  it('a plain line lays out exactly as before', () => {
    const model = flowModel(LINE, AGENTS)
    const plain = layout(model)
    expect(layout(model, { groups: stepGroups(LINE) })).toEqual(plain)
    expect(layout(model, { groups: stepGroups(LINE), mode: 'edit' })).toEqual(layout(model, { mode: 'edit' }))
    const plan = flowPlan(LINE, model)
    expect(plan.groups).toEqual([[0], [1], [2]])
    // every step sits on the middle line, its boxes where a column puts them
    expect(plan.ys.map((y) => y.center)).toEqual([plan.mid, plan.mid, plan.mid])
    expect(plan.ys[0].boxes).toEqual(boxOffsets(2).map((o) => plan.mid + o))
    // and each connector is the old one
    const h = plan.geo.height
    const n = [2, 1, 1]
    for (let k = 0; k <= 3; k++) {
      const shapes = connectorShapes(plan, k, 72)
      expect(shapes.length).toBe(1)
      const old = connectorPaths(k === 0 ? 1 : n[k - 1], k === 3 ? 1 : n[k], 72, h)
      expect({ lines: shapes[0].lines, arrows: shapes[0].arrows }).toEqual(old)
    }
  })
  it('puts steps in columns by depth and stacks the ones that run together', () => {
    expect(stepGroups(DIAMOND)).toEqual([[0], [1, 2], [3]])
    expect(stepDepths([{}, { after: [] }, { after: [0, 1] }, { after: [2] }])).toEqual([0, 0, 1, 2])
    const model = flowModel(DIAMOND, [])
    const plan = flowPlan(DIAMOND, model)
    expect(plan.geo.height).toBe(2 * 64 + 12)
    expect(plan.ys[1].center).toBeLessThan(plan.mid)
    expect(plan.ys[2].center).toBeGreaterThan(plan.mid)
    expect(plan.ys[1].center + plan.ys[2].center).toBe(plan.mid * 2)
  })
  it('draws a line from each step waited for', () => {
    const plan = flowPlan(DIAMOND, flowModel(DIAMOND, []))
    expect(flowEdges(DIAMOND)).toEqual([
      { from: 'start', to: 0 },
      { from: 0, to: 1 },
      { from: 0, to: 2 },
      { from: 1, to: 3 },
      { from: 2, to: 3 },
      { from: 3, to: 'end' },
    ])
    expect(connectorShapes(plan, 1, 72).map((s) => [s.from, s.to, s.kind])).toEqual([[0, 1, 'direct'], [0, 2, 'direct']])
    expect(connectorShapes(plan, 2, 72).map((s) => [s.from, s.to])).toEqual([[1, 3], [2, 3]])
  })
  it('a line that skips a column rides along the top', () => {
    const steps = [{ name: 'A' }, { name: 'B' }, { name: 'C', after: [0, 1] }]
    const plan = flowPlan(steps, flowModel(steps, []))
    expect(connectorShapes(plan, 1, 72).map((s) => s.kind)).toEqual(['direct', 'exit'])
    expect(connectorShapes(plan, 2, 72).map((s) => s.kind)).toEqual(['enter', 'direct'])
  })
})

describe('steps side by side: editing keeps the waits right', () => {
  const E = (name, after) => ({ name, kind: 'approval', message: 'm', ...(after ? { after } : {}) })
  const after = (list) => list.map((s) => s.after)
  it('a plain line is edited as before (no `after` appears)', () => {
    const line = [E('A'), E('B'), E('C')]
    expect(insertStep(line, 1, 'gate').map((s) => s.name)).toEqual(['A', 'Check', 'B', 'C'])
    expect(after(insertStep(line, 1, 'gate'))).toEqual([undefined, undefined, undefined, undefined])
    expect(moveStep(line, 0, 1).map((s) => s.name)).toEqual(['B', 'A', 'C'])
    expect(after(removeStep(line, 1))).toEqual([undefined, undefined])
    expect(moveBlocked(line, 0, 1)).toBeNull()
  })
  const DIAMOND = [E('A', []), E('B', [0]), E('C', [0]), E('D', [1, 2])]
  it('moving a step re-numbers every wait', () => {
    // B and C swap: both still wait for A; D still waits for both
    const m = moveStep(DIAMOND, 1, 1)
    expect(m.map((s) => s.name)).toEqual(['A', 'C', 'B', 'D'])
    expect(after(m)).toEqual([[], [0], [0], [1, 2]])
    const m2 = moveStep([E('A', []), E('X', []), E('B', [0]), E('D', [2, 1])], 1, 1)
    expect(m2.map((s) => s.name)).toEqual(['A', 'B', 'X', 'D'])
    expect(after(m2)).toEqual([[], [0], [], [1, 2]])
  })
  it('a step cannot go above a step it waits for (D waits for C, which is next to it)', () => {
    expect(moveBlocked(DIAMOND, 3, -1)).not.toBeNull()
    expect(moveStep(DIAMOND, 3, -1)).toEqual(DIAMOND)
  })
  it('refuses to move a step past one it waits for, in words', () => {
    const list = [E('Build', []), E('Tests', [0])]
    expect(moveBlocked(list, 1, -1)).toBe('“Tests” waits for “Build”, so it can\'t go above it.')
    expect(moveStep(list, 1, -1)).toEqual(list)
    expect(moveBlocked(list, 0, 1)).toBe('“Tests” waits for “Build”, so “Build” can\'t go below it.')
  })
  it('deleting a step removes it from every wait and re-numbers', () => {
    const d = removeStep(DIAMOND, 1)
    expect(d.map((s) => s.name)).toEqual(['A', 'C', 'D'])
    // D waited for B and C: it now also waits for what B waited for (A)
    expect(after(d)).toEqual([[], [0], [0, 1]])
    // never points later or nowhere
    d.forEach((s, i) => (s.after || []).forEach((j) => expect(j).toBeLessThan(i)))
  })
  it('a step left with nothing to wait for takes what the deleted one waited for', () => {
    const line = [E('A', []), E('B', [0]), E('C', [1])]
    expect(after(removeStep(line, 1))).toEqual([[], [0]])
    expect(removeNote(line, 1)).toBe('“C” now waits for what “B” waited for.')
    expect(removeNote([E('A', []), E('B', []), E('C', [1])], 1)).toBe('“C” now starts at once.')
    expect(removeNote(DIAMOND, 1)).toBeNull()
  })
  it('+ on a line puts the step in sequence', () => {
    // between A and B: the new step waits for A, B waits for the new step
    const ins = insertStep([E('A', []), E('B', [0]), E('C', [0])], 1, 'gate')
    expect(ins.map((s) => s.name)).toEqual(['A', 'Check', 'B', 'C'])
    expect(after(ins)).toEqual([[], [0], [1], [0]])
    // at the start the old first step now waits for the new one
    expect(after(insertStep([E('A', []), E('B', [0])], 0, 'gate'))).toEqual([[], [0], [1]])
    // at the end it waits for every last step
    expect(after(insertStep(DIAMOND, 4, 'gate'))).toEqual([[], [0], [0], [1, 2], [3]])
    const mid = insertStep(DIAMOND, 3, 'gate')
    expect(after(mid)).toEqual([[], [0], [0], [1, 2], [3]])
  })
})

describe('steps side by side: guards', () => {
  const E = (name, after) => ({ name, kind: 'approval', message: 'm', after })
  it('deleting a join never lets a step start earlier', () => {
    // A -> B; C waits for B and for Z (which starts at once); delete B
    const list = [E('A', []), E('Z', []), E('B', [0]), E('C', [1, 2])]
    const d = removeStep(list, 2)
    expect(d.map((s) => [s.name, s.after])).toEqual([['A', []], ['Z', []], ['C', [0, 1]]])
  })
  it('refuses to insert between steps that run side by side', () => {
    const diamond = [E('A', []), E('B', [0]), E('C', [0]), E('D', [1, 2])]
    expect(insertStep(diamond, 2, 'gate')).toEqual(diamond)
    expect(insertStep(diamond, 3, 'gate')).toHaveLength(5)
  })
})
