import { describe, expect, it } from 'vitest'
import {
  addAgent,
  boxKey,
  boxOffsets,
  columnHeight,
  connectorPaths,
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
