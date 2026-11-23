import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { flush, render } from '../home/testRender'

vi.mock('../api', () => ({
  api: {
    getWorkflowRun: vi.fn(),
    userDirectory: vi.fn(),
    cancelWorkflowRun: vi.fn(),
    retryWorkflowRun: vi.fn(),
    allowWorkflowGate: vi.fn(),
    approveWorkflowStage: vi.fn(),
    rejectWorkflowStage: vi.fn(),
    workflowGateLog: vi.fn(),
    raiseRunBudget: vi.fn(),
  },
  STATUS_COLORS: {},
}))
// The embedded TaskDetail opens a WebSocket; a stub keeps these tests about RunView.
vi.mock('../components/TaskDetail', () => ({
  default: ({ taskId }) => <div data-testid="task-detail-stub">{taskId}</div>,
}))

// Records the stages handed to the timeline, rendering the real component.
const seen = vi.hoisted(() => ({ stages: [] }))
vi.mock('../comets/Comets3D', async (importOriginal) => {
  const m = await importOriginal()
  const { createElement } = await import('react')
  return {
    ...m,
    default: (p) => {
      if (p.variant === 'timeline') seen.stages.push(p.stages)
      return createElement(m.default, p)
    },
  }
})

import { api } from '../api'
import RunView, { totalsLine } from './RunView'
import { canManageRun, defaultTaskFor, earlierAttemptTasks, runStages, stageState, tasksOfStage } from './runStages'

const err = (status, msg = 'x') => Object.assign(new Error(msg), { status })
const t = (id, stage_index, status, extra = {}) => ({ id, stage_index, status, title: id, ...extra })
const run = (over = {}) => ({
  id: 'r1',
  name: 'Feature flow',
  input: 'build teams page',
  status: 'running',
  current_stage: 1,
  stages: [{ name: 'Plan', items: [] }, { name: 'Develop', items: [] }, { name: 'Review', items: [] }],
  project_ids: ['p1'],
  created_by: 'u1',
  created_at: '2026-11-20T10:00:00Z',
  ...over,
})

describe('stageState (mirrors the backend LiveStageOut rules)', () => {
  it('all completed → done', () => {
    expect(stageState(['completed', 'completed'], 0, 2)).toBe('done')
  })
  it('any failed or cancelled → failed, before approval', () => {
    expect(stageState(['completed', 'failed'], 1, 1)).toBe('failed')
    expect(stageState(['cancelled', 'awaiting_approval'], 1, 1)).toBe('failed')
  })
  it('an approval wait → waiting_approval', () => {
    expect(stageState(['running', 'awaiting_approval'], 1, 1)).toBe('waiting_approval')
  })
  it('otherwise current at the run pointer, next elsewhere', () => {
    expect(stageState(['running'], 1, 1)).toBe('current')
    expect(stageState([], 1, 1)).toBe('current')
    expect(stageState([], 2, 1)).toBe('next')
    expect(stageState(['queued'], 2, 1)).toBe('next')
  })
  it('an earlier stage with no visible tasks → done', () => {
    expect(stageState([], 0, 2)).toBe('done')
    expect(stageState(undefined, 1, 2)).toBe('done')
    expect(runStages(run({ current_stage: 2 }), [t('c', 2, 'running')]).map((s) => s.state)).toEqual([
      'done',
      'done',
      'current',
    ])
  })
})

describe('runStages', () => {
  it('builds one stage per run stage with state and task count from the board', () => {
    const stages = runStages(run(), [t('a', 0, 'completed'), t('b', 0, 'completed'), t('c', 1, 'running')])
    expect(stages).toEqual([
      { name: 'Plan', state: 'done', task_count: 2 },
      { name: 'Develop', state: 'current', task_count: 1 },
      { name: 'Review', state: 'next', task_count: 0 },
    ])
  })
  it('maps failed and waiting-approval stages', () => {
    const stages = runStages(run({ status: 'paused' }), [t('a', 0, 'completed'), t('b', 1, 'awaiting_approval')])
    expect(stages.map((s) => s.state)).toEqual(['done', 'waiting_approval', 'next'])
    const failed = runStages(run({ status: 'failed' }), [t('a', 0, 'completed'), t('b', 1, 'failed')])
    expect(failed.map((s) => s.state)).toEqual(['done', 'failed', 'next'])
  })
  it('a completed run shows stages without visible tasks as done', () => {
    const stages = runStages(run({ status: 'completed', current_stage: 2 }), [t('a', 0, 'completed')])
    expect(stages.map((s) => s.state)).toEqual(['done', 'done', 'done'])
  })
  it('names unnamed stages and tolerates missing data', () => {
    expect(runStages(run({ stages: [{}] }), [])[0].name).toBe('Step 1')
    expect(runStages(null, null)).toEqual([])
  })
})

describe('retried stage with a real-shaped payload', () => {
  it('places the new attempt as current, as the server sends it', () => {
    const run = { status: 'running', current_stage: 0, stages: [{ name: 'S', items: [] }] }
    // TaskOut carries stage_attempt (0 for the failed first try, 1 after Retry)
    const tasks = [
      { id: 'a', stage_index: 0, stage_attempt: 0, status: 'failed' },
      { id: 'b', stage_index: 0, stage_attempt: 1, status: 'running' },
    ]
    const ss = { 0: { attempt: 1 } }
    expect(runStages(run, tasks, ss)).toEqual([{ name: 'S', state: 'current', task_count: 1 }])
    expect(tasksOfStage(tasks, 0, 1).map((t) => t.id)).toEqual(['b'])
    expect(earlierAttemptTasks(tasks, 0, 1).map((t) => t.id)).toEqual(['a'])
  })
})

describe('defaultTaskFor', () => {
  it('prefers a task that needs attention, then the newest', () => {
    expect(defaultTaskFor([t('a', 0, 'completed'), t('b', 0, 'running'), t('c', 0, 'completed')])).toBe('b')
    expect(defaultTaskFor([t('a', 0, 'completed'), t('c', 0, 'completed')])).toBe('c')
    expect(defaultTaskFor([])).toBeNull()
  })
})

describe('canManageRun (mirrors the backend cancel/retry rule)', () => {
  const r = run({ project_ids: ['p1', 'p2'] })
  it('admin, creator, or maintainer of a touched project', () => {
    expect(canManageRun(r, { id: 'x', role: 'admin' }, [])).toBe(true)
    expect(canManageRun(r, { id: 'u1', role: 'member' }, [])).toBe(true)
    expect(canManageRun(r, { id: 'x', role: 'member' }, [{ id: 'p2', my_role: 'maintainer' }])).toBe(true)
    expect(canManageRun(r, { id: 'x', role: 'member' }, [{ id: 'p2', my_role: 'contributor' }])).toBe(false)
    expect(canManageRun(r, { id: 'x', role: 'member' }, [{ id: 'p9', my_role: 'maintainer' }])).toBe(false)
  })
})

describe('RunView', () => {
  let view
  beforeAll(() => {
    window.matchMedia ||= () => ({ matches: true, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} })
  })
  beforeEach(() => {
    vi.clearAllMocks()
    api.userDirectory.mockImplementation(async () => [{ id: 'u1', name: 'Asha', email: 'a@x.io' }])
  })
  afterEach(async () => {
    await view?.unmount()
    view = null
  })
  const mount = async (props = {}) => {
    view = await render(
      <RunView id="r1" user={{ id: 'u1', role: 'member' }} projects={[{ id: 'p1', name: 'Canonizer', my_role: 'contributor' }]} navigate={() => {}} {...props} />,
    )
    await flush()
    await flush()
    return view.container
  }

  it.each([404, 403])('a %i shows the friendly not-found page with a link Home', async (status) => {
    api.getWorkflowRun.mockImplementation(async () => {
      throw err(status, 'Run not found')
    })
    const c = await mount()
    const nf = c.querySelector('[data-testid="run-not-found"]')
    expect(nf).not.toBeNull()
    expect(nf.textContent).toContain("Run not found or you don't have access")
    expect(nf.querySelector('a[href="#/home"]')).not.toBeNull()
    expect(c.querySelector('[data-testid="run-error"]')).toBeNull()
  })

  it('a network error shows an inline retry that reloads', async () => {
    api.getWorkflowRun.mockImplementationOnce(async () => {
      throw new TypeError('Failed to fetch')
    })
    const c = await mount()
    const box = c.querySelector('[data-testid="run-error"]')
    expect(box).not.toBeNull()
    expect(c.querySelector('[data-testid="run-not-found"]')).toBeNull()
    api.getWorkflowRun.mockImplementation(async () => ({ run: run(), tasks: [t('c', 1, 'running')] }))
    await act(async () => box.querySelector('button').click())
    await flush()
    expect(c.querySelector('[data-testid="run-title"]').textContent).toContain('Feature flow')
  })

  it('renders header, timeline and the current stage with its task embedded', async () => {
    api.getWorkflowRun.mockImplementation(async () => ({
      run: run(),
      tasks: [t('a', 0, 'completed', { title: '[Feature flow] Planner: plan it' }), t('c', 1, 'running', { title: '[Feature flow] Dev: build' })],
    }))
    const c = await mount()
    expect(c.querySelector('[data-testid="run-title"]').textContent).toContain('Feature flow')
    const sub = c.querySelector('[data-testid="run-subtitle"]').textContent
    expect(sub).toContain('Canonizer')
    expect(sub).toContain('you')
    // reduced motion (matchMedia true) → SVG timeline with 3 stage buttons
    const stages = c.querySelectorAll('[data-testid="timeline-stage"]')
    expect(stages.length).toBe(3)
    expect(stages[1].getAttribute('aria-pressed')).toBe('true')
    expect(c.querySelector('[data-testid="task-detail-stub"]').textContent).toBe('c')
    // Not manageable by a contributor who didn't start it? created_by is u1 = me → can cancel.
    expect(c.querySelector('[data-testid="run-cancel"]')).not.toBeNull()

    // Selecting the first stage shows its task.
    await act(async () => stages[0].click())
    expect(c.querySelector('[data-testid="task-detail-stub"]').textContent).toBe('a')
    expect(c.querySelector('[data-testid="run-stage-tasks"]').textContent).toContain('plan it')
  })

  it('hides cancel/retry from viewers who may not manage the run', async () => {
    api.getWorkflowRun.mockImplementation(async () => ({ run: run({ created_by: 'someone', status: 'failed' }), tasks: [] }))
    const c = await mount()
    expect(c.querySelector('[data-testid="run-cancel"]')).toBeNull()
    expect(c.querySelector('[data-testid="run-retry"]')).toBeNull()
  })

  it('takes the selected stage from props and reports clicks (URL-driven selection)', async () => {
    api.getWorkflowRun.mockImplementation(async () => ({
      run: run(),
      tasks: [t('a', 0, 'completed'), t('c', 1, 'running')],
    }))
    const onSelectStage = vi.fn()
    const c = await mount({ stage: 0, onSelectStage })
    let stages = c.querySelectorAll('[data-testid="timeline-stage"]')
    expect(stages[0].getAttribute('aria-pressed')).toBe('true')
    expect(c.querySelector('[data-testid="task-detail-stub"]').textContent).toBe('a')
    await act(async () => stages[2].click())
    expect(onSelectStage).toHaveBeenCalledWith(2)
    // the route (not local state) moves the selection: Back/Forward restore it
    expect(c.querySelectorAll('[data-testid="timeline-stage"]')[0].getAttribute('aria-pressed')).toBe('true')
    await view.rerender(
      <RunView id="r1" user={{ id: 'u1', role: 'member' }} projects={[]} navigate={() => {}} stage={1} onSelectStage={onSelectStage} />,
    )
    stages = c.querySelectorAll('[data-testid="timeline-stage"]')
    expect(stages[1].getAttribute('aria-pressed')).toBe('true')
    expect(c.querySelector('[data-testid="task-detail-stub"]').textContent).toBe('c')
    // an out-of-range stage falls back to the head stage
    await view.rerender(
      <RunView id="r1" user={{ id: 'u1', role: 'member' }} projects={[]} navigate={() => {}} stage={9} onSelectStage={onSelectStage} />,
    )
    expect(c.querySelectorAll('[data-testid="timeline-stage"]')[1].getAttribute('aria-pressed')).toBe('true')
  })

  it('keeps the same stages object across polls that change nothing', async () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] })
    try {
      api.getWorkflowRun.mockImplementation(async () => ({ run: run(), tasks: [t('c', 1, 'running')] }))
      await mount()
      const first = seen.stages.at(-1)
      seen.stages.length = 0
      await act(async () => vi.advanceTimersByTime(3100))
      await flush()
      expect(api.getWorkflowRun).toHaveBeenCalledTimes(2)
      expect(seen.stages.length).toBeGreaterThan(0)
      expect(seen.stages.every((s) => s === first)).toBe(true)
      // a real change does produce new stages
      api.getWorkflowRun.mockImplementation(async () => ({ run: run(), tasks: [t('c', 1, 'completed')] }))
      await act(async () => vi.advanceTimersByTime(3100))
      await flush()
      expect(seen.stages.at(-1)).not.toBe(first)
      expect(seen.stages.at(-1)[1].state).toBe('done')
    } finally {
      vi.useRealTimers()
    }
  })

  it('marks a stage whose agents run in parallel', async () => {
    api.getWorkflowRun.mockImplementation(async () => ({
      run: run({ stages: [{ name: 'Plan', items: [{ agent: 'A' }] }, { name: 'Develop', items: [{ agent: 'B' }, { agent: 'C' }] }] }),
      tasks: [t('c', 1, 'running')],
    }))
    const c = await mount()
    const head = c.querySelector('[data-testid="run-stage-tasks"] header')
    expect(head.querySelector('[data-testid="run-stage-parallel"]')?.textContent).toBe('2 agents at once')
  })

  it('a gate stage renders GateCard (timeline still renders) and the totals line shows', async () => {
    api.getWorkflowRun.mockImplementation(async () => ({
      run: run({ current_stage: 1, stages: [{ name: 'Plan', items: [] }, { name: 'Tests', kind: 'gate', run: ['pytest -q'], project: 'p1' }] }),
      tasks: [t('a', 0, 'completed')],
      stage_state: { 1: { status: 'running', attempt: 0, passed_cmds: [], status_text: 'not yet passed (0 of 1 commands passed, none running)' } },
      gate_runs: [],
      live: null,
      totals: { line: 'So far: $1.23 over 2 tasks' },
      log: [{ at: '2026-11-20T10:00:00Z', text: 'first line' }, { at: '2026-11-20T10:01:00Z', text: 'second line' }],
    }))
    const c = await mount()
    expect(c.querySelector('[data-testid="gate-card"]')).not.toBeNull()
    expect(c.querySelector('[data-testid="gate-words"]').textContent).toContain('not yet passed (0 of 1 commands passed')
    expect(c.querySelector('[data-testid="run-totals-line"]').textContent).toBe('So far: $1.23 over 2 tasks')
    expect(seen.stages.at(-1).map((s) => s.task_count)).toEqual([1, 0])
    const lines = [...c.querySelectorAll('[data-testid="run-log"] li')].map((l) => l.textContent)
    expect(lines[0]).toContain('second line')
  })

  it('earlier attempts are collapsed under "Earlier attempts (1)"', async () => {
    api.getWorkflowRun.mockImplementation(async () => ({
      run: run(),
      tasks: [t('old', 1, 'failed', { stage_attempt: 0 }), t('new', 1, 'running', { stage_attempt: 1 })],
      stage_state: { 1: { status: 'running', attempt: 1 } },
    }))
    const c = await mount()
    const d = c.querySelector('[data-testid="run-earlier"]')
    expect(d.querySelector('summary').textContent).toBe('Earlier attempts (1)')
    expect(d.open).toBe(false)
    const outer = [...c.querySelectorAll('.co-run-tasks')].filter((u) => !d.contains(u))
    expect(outer[0].querySelectorAll('[data-testid="run-task"]').length).toBe(1)
    expect(seen.stages.at(-1)[1]).toMatchObject({ state: 'current', task_count: 1 })
  })

  it('a run paused on an approval step points at the step, and the step panel is full width', async () => {
    api.getWorkflowRun.mockImplementation(async () => ({
      run: run({ status: 'paused', current_stage: 1, stages: [{ name: 'Tests', kind: 'gate', run: ['x'] }, { name: 'Sign-off', kind: 'approval', message: 'ok?' }] }),
      tasks: [],
      stage_state: { 0: { status: 'passed' }, 1: { status: 'waiting_approval' } },
      totals: { stages_done: 1, stages: 2, minutes: 1.6, cost_usd: 0, tokens: 0, line: 'So far: …' },
    }))
    const c = await mount()
    expect(c.querySelector('[data-testid="run-paused"]').textContent).toBe('Waiting for someone to approve the step below.')
    expect(c.querySelector('[data-testid="run-body"]').className).toContain('co-run-body-full')
    expect(c.querySelector('[data-testid="run-stage-state"]')).toBeNull()
    expect(c.querySelector('[data-testid="run-status"]').textContent).toBe('Waiting for you')
    expect(c.querySelector('[data-testid="run-totals-line"]').textContent).toBe('1 of 2 steps done · 1.6 min')
    expect(c.querySelector('[data-testid="run-task-count"]')).toBeNull()
  })

  it('a run paused on a task approval keeps the Approvals wording', async () => {
    api.getWorkflowRun.mockImplementation(async () => ({
      run: run({ status: 'paused' }),
      tasks: [t('c', 1, 'awaiting_approval')],
    }))
    const c = await mount()
    expect(c.querySelector('[data-testid="run-paused"]').textContent).toContain('decide it in Approvals')
    expect(c.querySelector('[data-testid="run-stage-state"]').textContent).toBe('Waiting for approval')
    expect(c.querySelector('[data-testid="run-task-count"]')).not.toBeNull()
  })
})

describe('RunView: hand-overs, fixes and the spending limit', () => {
  let view
  beforeAll(() => {
    window.matchMedia ||= () => ({ matches: true, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} })
  })
  beforeEach(() => {
    vi.clearAllMocks()
    api.userDirectory.mockImplementation(async () => [])
  })
  afterEach(async () => {
    await view?.unmount()
    view = null
  })
  const mount = async (user = { id: 'u1', role: 'member' }) => {
    view = await render(<RunView id="r1" user={user} projects={[{ id: 'p1', name: 'Canonizer', my_role: 'contributor' }]} navigate={() => {}} />)
    await flush()
    await flush()
    return view.container
  }

  it('lists what an agent handed over, as text', async () => {
    api.getWorkflowRun.mockImplementation(async () => ({
      run: run({ current_stage: 0, stages: [{ name: 'Plan', items: [{ agent: 'Planner', prompt: 'p', outputs: ['plan_path', 'summary'] }] }] }),
      tasks: [t('a', 0, 'completed', { title: '[Feature flow] Planner: plan', outputs: { plan_path: 'docs/plan.md', summary: '<b>bold</b>\u202Eevil' } })],
    }))
    const c = await mount()
    const box = c.querySelector('[data-testid="run-handed-over"]')
    expect(box.textContent).toContain('Handed over')
    const rows = [...box.querySelectorAll('[data-testid="run-handed-row"]')]
    expect(rows.map((r) => r.querySelector('dt').textContent)).toEqual(['plan_path', 'summary'])
    expect(rows[0].querySelector('dd').textContent).toBe('docs/plan.md')
    expect(rows[1].querySelector('b')).toBeNull()
    expect(rows[1].querySelector('dd').textContent).toContain('<b>bold</b>')
    expect(rows[1].querySelector('dd').textContent).toContain('U+202E')
  })

  it('no hand-over list when no task handed anything over', async () => {
    api.getWorkflowRun.mockImplementation(async () => ({ run: run(), tasks: [t('c', 1, 'running')] }))
    const c = await mount()
    expect(c.querySelector('[data-testid="run-handed-over"]')).toBeNull()
  })

  it('a check being fixed leads with the try and opens the fix task only on request', async () => {
    api.getWorkflowRun.mockImplementation(async () => ({
      run: run({ current_stage: 1, stages: [{ name: 'Plan', items: [] }, { name: 'Tests', kind: 'gate', run: ['pytest -q'], project: 'p1', fix: { agent: 'ag1', rounds: 2 } }] }),
      tasks: [t('a', 0, 'completed'), t('fx', 1, 'running', { title: '[Tests] Fixer: fix round 1' })],
      stage_state: { 1: { status: 'fixing', fix_round: 1, fix_task: 'fx', attempt: 0 } },
      gate_runs: [],
    }))
    const c = await mount()
    expect(c.querySelector('[data-testid="gate-lead"]').textContent).toBe('The check failed. An agent is fixing it (try 1 of 2).')
    expect(c.querySelector('[data-testid="task-detail-stub"]')).toBeNull()
    await act(async () => c.querySelector('[data-testid="gate-fix-task"]').click())
    expect(c.querySelector('[data-testid="task-detail-stub"]').textContent).toBe('fx')
  })

  it('a passed check keeps its fix attempts reachable, earlier attempts included', async () => {
    api.getWorkflowRun.mockImplementation(async () => ({
      run: run({ current_stage: 1, stages: [{ name: 'Plan', items: [] }, { name: 'Tests', kind: 'gate', run: ['pytest -q'], project: 'p1', fix: { agent: 'ag1', rounds: 2 } }] }),
      tasks: [
        t('a', 0, 'completed'),
        t('old', 1, 'failed', { stage_attempt: 0, title: '[Tests] Fixer: fix round 1' }),
        t('fx', 1, 'completed', { stage_attempt: 1, title: '[Tests] Fixer: fix round 1' }),
      ],
      stage_state: { 1: { status: 'passed', fix_round: 1, fix_task: 'fx', attempt: 1 } },
      gate_runs: [],
    }))
    const c = await mount()
    const rows = [...c.querySelectorAll('[data-testid="gate-fix-attempt"]')]
    expect(rows.length).toBe(2)
    expect(c.querySelector('[data-testid="task-detail-stub"]')).toBeNull()
    await act(async () => rows[0].querySelector('[data-testid="gate-fix-attempt-open"]').click())
    expect(c.querySelector('[data-testid="task-detail-stub"]').textContent).toBe('old')
  })

  it('shows the spending in the header', async () => {
    api.getWorkflowRun.mockImplementation(async () => ({ run: run(), tasks: [t('c', 1, 'running')], budget: { spent: 0.5, budget_usd: 2 } }))
    const c = await mount()
    expect(c.querySelector('[data-testid="run-budget-line"]').textContent).toBe('Spent $0.50 of $2.00')
    expect(c.querySelector('[data-testid="run-budget-panel"]')).toBeNull()
  })

  it('no spending line without a limit', async () => {
    api.getWorkflowRun.mockImplementation(async () => ({ run: run(), tasks: [], budget: { spent: 0.5, budget_usd: null } }))
    const c = await mount()
    expect(c.querySelector('[data-testid="run-budget-line"]')).toBeNull()
  })

  const held = () => ({
    run: run({ status: 'paused', current_stage: 1 }),
    tasks: [t('a', 0, 'completed')],
    stage_state: { 1: { status: 'budget', reason: 'budget reached ($1.25 of $1.00)', attempt: 0 } },
    budget: { spent: 1.25, budget_usd: 1 },
  })

  it('a step held by the limit shows the panel; a manager raises it', async () => {
    api.getWorkflowRun.mockImplementation(async () => held())
    api.raiseRunBudget.mockResolvedValue({})
    const c = await mount()
    const panel = c.querySelector('[data-testid="run-budget-panel"]')
    expect(panel.textContent).toContain('This run reached its spending limit ($1.25 of $1.00).')
    expect(c.querySelector('[data-testid="run-paused"]')).toBeNull()
    const raise = c.querySelector('[data-testid="run-budget-raise"]')
    expect(raise.textContent).toBe('Raise the limit and continue')
    expect(raise.disabled).toBe(true)
    const input = c.querySelector('input[aria-label="New limit"]')
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, '3')
      input.dispatchEvent(new Event('input', { bubbles: true }))
    })
    await flush()
    await act(async () => c.querySelector('[data-testid="run-budget-raise"]').click())
    await flush()
    expect(api.raiseRunBudget).toHaveBeenCalledWith('r1', 3)
    expect(api.getWorkflowRun.mock.calls.length).toBeGreaterThanOrEqual(2)
  })

  it("a refused raise shows the server's reason", async () => {
    api.getWorkflowRun.mockImplementation(async () => held())
    api.raiseRunBudget.mockRejectedValue(err(409, 'the budget must be more than the $1.25 already spent'))
    const c = await mount()
    const input = c.querySelector('input[aria-label="New limit"]')
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, '1.1')
      input.dispatchEvent(new Event('input', { bubbles: true }))
    })
    await flush()
    await act(async () => c.querySelector('[data-testid="run-budget-raise"]').click())
    await flush()
    expect(c.querySelector('[data-testid="run-budget-error"]').textContent).toContain('the budget must be more than the $1.25 already spent')
  })

  it('people who cannot manage the run see the panel without the raise', async () => {
    api.getWorkflowRun.mockImplementation(async () => ({ ...held(), run: run({ status: 'paused', current_stage: 1, created_by: 'someone' }) }))
    const c = await mount()
    expect(c.querySelector('[data-testid="run-budget-panel"]')).not.toBeNull()
    expect(c.querySelector('[data-testid="run-budget-raise"]')).toBeNull()
  })
})

describe('totalsLine', () => {
  it('adds cost and tokens only when above zero', () => {
    expect(totalsLine({ stages_done: 1, stages: 2, minutes: 1.6, cost_usd: 0, tokens: 0 })).toBe('1 of 2 steps done · 1.6 min')
    expect(totalsLine({ stages_done: 2, stages: 2, minutes: 12.4, cost_usd: 2.1, tokens: 1_200_000 })).toBe('2 of 2 steps done · 12 min · $2.10 · 1.2M tokens')
    expect(totalsLine({ stages_done: 0, stages: 1, minutes: 0, cost_usd: 0, tokens: 41_200 })).toBe('0 of 1 step done · 0.0 min · 41k tokens')
    expect(totalsLine({ line: 'server line' })).toBe('server line')
    expect(totalsLine(null)).toBeNull()
  })
})
