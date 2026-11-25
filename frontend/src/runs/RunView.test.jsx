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
    rerunWorkflowStage: vi.fn(),
    approveAhead: vi.fn(),
    withdrawAhead: vi.fn(),
  },
  STATUS_COLORS: {},
}))
// The embedded TaskDetail opens a WebSocket; a stub keeps these tests about RunView.
vi.mock('../components/TaskDetail', () => ({
  default: ({ taskId }) => <div data-testid="task-detail-stub">{taskId}</div>,
}))

import { api } from '../api'
import RunView, { totalsLine } from './RunView'
import { canManageRun, defaultTaskFor, failedStepIndex, earlierAttemptTasks, runStages, stageState, tasksOfStage } from './runStages'

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
    // the flow canvas: one box per step (these steps list no agents)
    const stages = c.querySelectorAll('[data-testid="flow-box"]')
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
    let stages = c.querySelectorAll('[data-testid="flow-box"]')
    expect(stages[0].getAttribute('aria-pressed')).toBe('true')
    expect(c.querySelector('[data-testid="task-detail-stub"]').textContent).toBe('a')
    await act(async () => stages[2].click())
    expect(onSelectStage).toHaveBeenCalledWith(2)
    // the route (not local state) moves the selection: Back/Forward restore it
    expect(c.querySelectorAll('[data-testid="flow-box"]')[0].getAttribute('aria-pressed')).toBe('true')
    await view.rerender(
      <RunView id="r1" user={{ id: 'u1', role: 'member' }} projects={[]} navigate={() => {}} stage={1} onSelectStage={onSelectStage} />,
    )
    stages = c.querySelectorAll('[data-testid="flow-box"]')
    expect(stages[1].getAttribute('aria-pressed')).toBe('true')
    expect(c.querySelector('[data-testid="task-detail-stub"]').textContent).toBe('c')
    // an out-of-range stage falls back to the head stage
    await view.rerender(
      <RunView id="r1" user={{ id: 'u1', role: 'member' }} projects={[]} navigate={() => {}} stage={9} onSelectStage={onSelectStage} />,
    )
    expect(c.querySelectorAll('[data-testid="flow-box"]')[1].getAttribute('aria-pressed')).toBe('true')
  })

  it('a poll that changes a task updates its box on the canvas', async () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] })
    try {
      api.getWorkflowRun.mockImplementation(async () => ({ run: run(), tasks: [t('c', 1, 'running')] }))
      const c = await mount()
      const status = () => [...c.querySelectorAll('[data-testid="flow-box"]')].map((b) => b.dataset.status)
      expect(status()).toEqual(['done', 'running', 'next'])
      api.getWorkflowRun.mockImplementation(async () => ({ run: run(), tasks: [t('c', 1, 'completed')] }))
      await act(async () => vi.advanceTimersByTime(3100))
      await flush()
      expect(status()[1]).toBe('done')
    } finally {
      vi.useRealTimers()
    }
  })

  it('draws the run as a flow: each agent has its own status, and clicking it opens its task', async () => {
    api.getWorkflowRun.mockImplementation(async () => ({
      run: run({
        current_stage: 1,
        stages: [
          { name: 'Plan', items: [{ agent: 'Planner' }] },
          { name: 'Develop', items: [{ agent: 'Dev' }, { agent: 'Docs' }] },
          { name: 'Tests', kind: 'gate', run: ['npm test'], project: 'p1' },
          { name: 'Sign', kind: 'approval' },
        ],
      }),
      tasks: [
        t('a', 0, 'completed', { title: '[Plan] Planner: plan' }),
        t('d', 1, 'running', { title: '[Develop] Dev: build' }),
        t('e', 1, 'completed', { title: '[Develop] Docs: write' }),
      ],
    }))
    const onSelectStage = vi.fn()
    const c = await mount({ stage: 1, onSelectStage })
    const canvas = c.querySelector('.co-run-timeline [data-testid="flow-canvas"]')
    expect(canvas.className).toContain('co-flow-mode-run')
    expect(canvas.className).toContain('co-flow-center')
    const boxes = [...canvas.querySelectorAll('[data-testid="flow-box"]')]
    expect(boxes.map((b) => b.getAttribute('aria-label'))).toEqual([
      'Step 1, Agent: Planner, done',
      'Step 2, Agent 1 of 2: Dev, running',
      'Step 2, Agent 2 of 2: Docs, done',
      'Step 3, Check: Tests, not started',
      'Step 4, Ask a person: Sign, not started',
    ])
    expect(boxes[1].className).toContain('co-flow-st-running')
    // the selected step: both of its agents
    expect(boxes.map((b) => b.getAttribute('aria-pressed'))).toEqual(['false', 'true', 'true', 'false', 'false'])
    expect(canvas.querySelector('[aria-label="Start, running on: build teams page"]')).not.toBeNull()
    // the default task of the step is the running one; clicking Docs opens Docs's task
    expect(c.querySelector('[data-testid="task-detail-stub"]').textContent).toBe('d')
    await act(async () => boxes[2].click())
    expect(onSelectStage).toHaveBeenCalledWith(1)
    expect(c.querySelector('[data-testid="task-detail-stub"]').textContent).toBe('e')
    // the line into the running step is coloured, later ones muted
    const links = [...canvas.querySelectorAll('[data-testid="flow-link"]')].map((l) => l.className)
    expect(links[0]).toContain('reached')
    expect(links[1]).toContain('reached')
    expect(links[2]).toContain('muted')
    expect(links[4]).toContain('muted')
  })

  it('a check being fixed shows as failed with its fixer running; the fixer opens the fix task', async () => {
    api.getWorkflowRun.mockImplementation(async () => ({
      run: run({ current_stage: 1, stages: [{ name: 'Plan', items: [] }, { name: 'Tests', kind: 'gate', run: ['pytest -q'], project: 'p1', fix: { agent: 'ag1', rounds: 2 } }] }),
      tasks: [t('a', 0, 'completed'), t('fx', 1, 'running', { title: '[Tests] Fixer: fix round 1' })],
      stage_state: { 1: { status: 'fixing', fix_round: 1, fix_task: 'fx', attempt: 0 } },
      gate_runs: [],
    }))
    const c = await mount()
    const boxes = [...c.querySelectorAll('[data-testid="flow-box"]')]
    expect(boxes.map((b) => [b.dataset.kind, b.dataset.status])).toEqual([
      ['agent', 'done'],
      ['gate', 'failed'],
      ['fix', 'running'],
    ])
    expect(c.querySelector('[data-testid="task-detail-stub"]')).toBeNull()
    await act(async () => boxes[2].click())
    expect(c.querySelector('[data-testid="task-detail-stub"]').textContent).toBe('fx')
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

  it('a gate stage renders GateCard (the canvas still renders) and the totals line shows', async () => {
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
    expect([...c.querySelectorAll('[data-testid="flow-box"]')].map((b) => b.dataset.status)).toEqual(['done', 'running'])
    const lines = [...c.querySelectorAll('[data-testid="run-log"] li')].map((l) => l.textContent)
    expect(lines[0]).toContain('second line')
  })

  it('a step held for the Claude plan shows its own start time in local time', async () => {
    const nb = new Date(Date.now() + 3 * 3600e3).toISOString()
    api.getWorkflowRun.mockImplementation(async () => ({
      run: run(),
      tasks: [],
      stage_state: { 1: { status: 'held', plan_wait: true, reason: 'Paused — Starts again at 03:33.', not_before: nb } },
    }))
    const c = await mount()
    const el = c.querySelector('[data-testid="run-stage-plan-wait"]')
    expect(el.textContent).toMatch(/^Paused — your Claude plan limit was reached\. Starts again at (tomorrow |\d+ \w+, )?\d{2}:\d{2}\.$/)
    expect(el.textContent).not.toContain('03:33')
    expect(c.querySelector('[data-testid="run-stage-state"]').textContent).toBe('Waiting for plan limit')
    expect(c.textContent).not.toContain('Not started yet')
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
    expect(c.querySelectorAll('[data-testid="flow-box"]')[1].dataset.status).toBe('running')
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

  it('reveals hidden characters in the agent name of a hand-over', async () => {
    api.getWorkflowRun.mockImplementation(async () => ({
      run: run({ current_stage: 0, stages: [{ name: 'Plan', items: [] }] }),
      tasks: [
        t('a', 0, 'completed', { title: '[F] Pl\u202Ean: x', outputs: { s: '1' } }),
        t('b', 0, 'completed', { title: '[F] B: y', outputs: { s: '2' } }),
      ],
    }))
    const c = await mount()
    expect(c.querySelector('.co-run-handed-agent').textContent).toContain('⟦U+202E⟧')
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

describe('RunView: run control (approve ahead, run again, time and spend)', () => {
  let v
  beforeAll(() => {
    window.matchMedia ||= () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} })
  })
  beforeEach(() => vi.clearAllMocks())
  afterEach(async () => {
    await v?.unmount()
    v = null
  })
  const mount = async (user = { id: 'u1', role: 'member' }) => {
    v = await render(<RunView id="r1" user={user} projects={[{ id: 'p1', name: 'Canonizer', my_role: 'contributor' }]} navigate={() => {}} />)
    await flush()
    await flush()
    return v.container
  }
  const press = async (el) => {
    await act(async () => el.dispatchEvent(new MouseEvent('click', { bubbles: true })))
    await flush()
  }
  const STAGES = [{ name: 'Plan', items: [{ agent: 'Planner', prompt: 'p' }] }, { name: 'Sign-off', kind: 'approval', message: 'ok?', ahead: true }, { name: 'Ship', kind: 'approval' }]
  const base = (over = {}) => ({
    run: run({ status: 'running', current_stage: 0, stages: STAGES, ...(over.run || {}) }),
    tasks: [t('a', 0, 'running', { title: '[Feature flow] Planner: plan' })],
    stage_state: over.stage_state || {},
    step_usage: over.step_usage,
  })
  const pickBox = (c, n) => press([...c.querySelectorAll('[data-testid="flow-box"]')][n])

  it('Approve ahead shows on a later approval step for a manager and calls the api after asking', async () => {
    api.getWorkflowRun.mockImplementation(async () => base())
    api.approveAhead.mockResolvedValue({})
    const c = await mount()
    await pickBox(c, 1)
    await press(c.querySelector('[data-testid="approval-ahead-btn"]'))
    expect(document.querySelector('.ant-popconfirm').textContent).toContain('Approve Sign-off now? The run will go straight past it.')
    await press([...document.querySelector('.ant-popconfirm').querySelectorAll('button')].find((b) => b.textContent === 'Approve ahead'))
    expect(api.approveAhead).toHaveBeenCalledWith('r1', 1)
    expect(api.getWorkflowRun.mock.calls.length).toBeGreaterThan(1) // refetched
  })

  it('a step that never allows it says it needs you; a non-manager sees no button', async () => {
    api.getWorkflowRun.mockImplementation(async () => base())
    let c = await mount()
    await pickBox(c, 2)
    expect(c.querySelector('[data-testid="approval-ahead-btn"]')).toBeNull()
    expect(c.querySelector('[data-testid="approval-needs-you"]').textContent).toBe('Needs you when the run gets here')
    await v.unmount()
    c = await mount({ id: 'u9', role: 'member' })
    await pickBox(c, 1)
    expect(c.querySelector('[data-testid="approval-ahead-btn"]')).toBeNull()
  })

  it('shows "Approved ahead by <name>" and Withdraw on a step that was approved ahead', async () => {
    api.getWorkflowRun.mockImplementation(async () => base({ stage_state: { 1: { ahead: { by_name: 'Asha', at: '2026-11-25T10:00:00Z' } } } }))
    api.withdrawAhead.mockResolvedValue({})
    const c = await mount()
    await pickBox(c, 1)
    expect(c.querySelector('[data-testid="approval-ahead"]').textContent).toContain('Approved ahead by Asha')
    await press(c.querySelector('[data-testid="approval-withdraw"]'))
    expect(api.withdrawAhead).toHaveBeenCalledWith('r1', 1)
  })

  it('Run again from here: only on a finished run, asks first, refetches', async () => {
    const done = () => base({ run: { status: 'completed', current_stage: 2 }, stage_state: { 1: { status: 'approved' }, 2: { status: 'approved' } } })
    api.getWorkflowRun.mockImplementation(async () => done())
    api.rerunWorkflowStage.mockResolvedValue({})
    const c = await mount()
    await pickBox(c, 0)
    await press(c.querySelector('[data-testid="run-rerun-btn"]'))
    expect(document.querySelector('.ant-popconfirm').textContent).toContain('Run again from Plan? Later steps start over.')
    await press([...document.querySelector('.ant-popconfirm').querySelectorAll('button')].find((b) => b.textContent === 'Run again'))
    expect(api.rerunWorkflowStage).toHaveBeenCalledWith('r1', 0)
  })

  it('no Run again on a running run, for non-managers, or on a step not reached', async () => {
    api.getWorkflowRun.mockImplementation(async () => base())
    let c = await mount()
    expect(c.querySelector('[data-testid="run-rerun-btn"]')).toBeNull()
    await v.unmount()
    api.getWorkflowRun.mockImplementation(async () => base({ run: { status: 'failed', current_stage: 0 } }))
    c = await mount()
    await pickBox(c, 1)
    expect(c.querySelector('[data-testid="run-rerun-btn"]')).toBeNull()
    await v.unmount()
    c = await mount({ id: 'u9', role: 'member' })
    await pickBox(c, 0)
    expect(c.querySelector('[data-testid="run-rerun-btn"]')).toBeNull()
  })

  it('a refused rerun shows the server sentence; other failures a plain one', async () => {
    api.getWorkflowRun.mockImplementation(async () => base({ run: { status: 'completed', current_stage: 2 } }))
    const m = vi.spyOn((await import('antd')).message, 'error').mockImplementation(() => {})
    api.rerunWorkflowStage.mockRejectedValue(err(409, 'the run is still going; it can run again once it has finished'))
    const c = await mount()
    await pickBox(c, 0)
    await press(c.querySelector('[data-testid="run-rerun-btn"]'))
    await press([...document.querySelector('.ant-popconfirm').querySelectorAll('button')].find((b) => b.textContent === 'Run again'))
    expect(m).toHaveBeenCalledWith('the run is still going; it can run again once it has finished')
    m.mockRestore()
  })

  it('shows time and spend on the box, tokens only under Details, and status words', async () => {
    api.getWorkflowRun.mockImplementation(async () =>
      base({
        step_usage: [{ cost_usd: 0.123, input_tokens: 1200, output_tokens: 300, seconds: 250 }, null, null],
        stage_state: { 0: { status_word: 'Waiting its turn' } },
      }),
    )
    const c = await mount()
    const lines = [...c.querySelectorAll('[data-testid="flow-usage"]')].map((x) => x.textContent)
    expect(lines).toEqual(['4 min · $0.12'])
    expect(c.querySelector('[data-testid="run-stage-usage"]').textContent).toBe('4 min · $0.12')
    const details = c.querySelector('[data-testid="run-stage-details"]')
    expect(details.open).toBe(false)
    expect(details.textContent).toContain('1.2k tokens in')
    expect(c.querySelector('.co-run-timeline').textContent).not.toMatch(/tokens/)
    expect(c.querySelector('[data-testid="run-stage-state"]').textContent).toBe('Waiting its turn')
    expect(c.querySelector('[data-testid="flow-box"]').getAttribute('aria-label')).toContain('Waiting its turn')
  })

  it('marks a step approved ahead on the canvas, and the card says so once', async () => {
    api.getWorkflowRun.mockImplementation(async () => base({ stage_state: { 1: { ahead: { by_name: 'Asha' } } } }))
    const c = await mount()
    const box = [...c.querySelectorAll('[data-testid="flow-box"]')][1]
    expect(box.querySelector('[data-testid="flow-ahead"]')).not.toBeNull()
    expect(box.getAttribute('aria-label')).toContain('approved ahead')
    expect([...c.querySelectorAll('[data-testid="flow-box"]')][2].querySelector('[data-testid="flow-ahead"]')).toBeNull()
    await pickBox(c, 1)
    expect(c.querySelector('[data-testid="approval-lead"]').textContent).toBe('Approved ahead')
    expect(c.querySelector('[data-testid="approval-ahead-note"]').textContent).toBe('The run will go straight past this step.')
    expect(c.querySelector('[data-testid="run-stage-state"]')).toBeNull() // the card heading already says it
  })

  it('a step status word goes on the main box only, not the fixer or a parallel agent', async () => {
    api.getWorkflowRun.mockImplementation(async () => ({
      run: run({ current_stage: 0, stages: [{ name: 'Two', items: [{ agent: 'A', prompt: 'p' }, { agent: 'B', prompt: 'p' }] }] }),
      tasks: [],
      stage_state: { 0: { status_word: 'Failed' } },
    }))
    const c = await mount()
    for (const b of c.querySelectorAll('[data-testid="flow-box"]')) expect(b.getAttribute('aria-label')).not.toContain('Failed')
  })
})

describe('RunView: steps side by side', () => {
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

  it('shows every running step as running and counts steps done as before', async () => {
    const stages = [
      { name: 'Build', items: [{ agent: 'a', prompt: 'x' }] },
      { name: 'Write tests', items: [{ agent: 'a', prompt: 'x' }], after: [0] },
      { name: 'Write docs', items: [{ agent: 'a', prompt: 'x' }], after: [0] },
      { name: 'Check', kind: 'gate', run: ['pytest'], project: 'p1', after: [1, 2] },
    ]
    api.getWorkflowRun.mockImplementation(async () => ({
      run: run({ stages, current_stage: 1 }),
      tasks: [t('a', 0, 'completed'), t('b', 1, 'running'), t('c', 2, 'running')],
      stage_state: { 0: { status: 'done' } },
      totals: { stages_done: 1, stages: 4, minutes: 3, cost_usd: 0, tokens: 0 },
    }))
    view = await render(<RunView id="r1" user={{ id: 'u1', role: 'member' }} projects={[{ id: 'p1', name: 'Canonizer', my_role: 'contributor' }]} navigate={() => {}} />)
    await flush()
    await flush()
    const c = view.container
    const boxes = [...c.querySelectorAll('[data-testid="flow-box"]')]
    expect(boxes.map((b) => b.dataset.status)).toEqual(['done', 'running', 'running', 'next'])
    expect(c.querySelector('[data-testid="flow-group"]').querySelectorAll('[data-testid="flow-step"]').length).toBe(2)
    expect(c.querySelector('[data-testid="run-totals-line"]').textContent).toBe('1 of 4 steps done · 3.0 min')
  })

  it('a step running above the lowest open step is running, not waiting', () => {
    const r = { current_stage: 0, status: 'running', stages: [{ name: 'A', items: [] }, { name: 'B', items: [] }, { name: 'C', items: [] }] }
    expect(runStages(r, [t('x', 0, 'running'), t('y', 2, 'queued')], {}).map((s) => s.state)).toEqual(['current', 'next', 'current'])
    const g = { current_stage: 0, status: 'running', stages: [{ name: 'A', items: [] }, { name: 'G', kind: 'gate', run: ['x'] }] }
    expect(runStages(g, [t('x', 0, 'running')], { 1: { status: 'running' } }).map((s) => s.state)).toEqual(['current', 'current'])
  })
})

describe('RunView: a failed run opens on the step that failed', () => {
  it('failedStepIndex skips a step only cancelled with it', () => {
    const r = { current_stage: 1, status: 'failed', stages: [{ name: 'A', items: [] }, { name: 'W', items: [] }, { name: 'T', kind: 'gate', run: ['x'] }] }
    expect(failedStepIndex(r, [t('w', 1, 'cancelled')], { 2: { status: 'failed' } })).toBe(2)
    expect(failedStepIndex(r, [t('w', 1, 'failed')], {})).toBe(1)
    expect(failedStepIndex(r, [t('w', 1, 'cancelled')], {})).toBe(-1)
    expect(failedStepIndex(r, [], { 2: { status: 'rejected' } })).toBe(2)
  })
  it('selects it by default', async () => {
    const stages = [
      { name: 'Build', items: [{ agent: 'a', prompt: 'x' }] },
      { name: 'Write docs', items: [{ agent: 'a', prompt: 'x' }], after: [0] },
      { name: 'Test', kind: 'gate', run: ['pytest'], project: 'p1', after: [0] },
    ]
    api.getWorkflowRun.mockImplementation(async () => ({
      run: run({ stages, status: 'failed', current_stage: 1 }),
      tasks: [t('a', 0, 'completed'), t('b', 1, 'cancelled')],
      stage_state: { 0: { status: 'done' }, 2: { status: 'failed' } },
    }))
    const v = await render(<RunView id="r1" user={{ id: 'u1', role: 'member' }} projects={[{ id: 'p1', name: 'Canonizer', my_role: 'contributor' }]} navigate={() => {}} />)
    await flush()
    await flush()
    const boxes = [...v.container.querySelectorAll('[data-testid="flow-box"]')]
    expect(boxes.map((b) => b.getAttribute('aria-pressed'))).toEqual(['false', 'false', 'true'])
    await v.unmount()
  })
})

describe('RunView: reserve holds, stopped branches, run again', () => {
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
  const mount = async () => {
    view = await render(<RunView id="r1" user={{ id: 'u1', role: 'member' }} projects={[{ id: 'p1', name: 'Canonizer', my_role: 'contributor' }]} navigate={() => {}} />)
    await flush()
    await flush()
    return view.container
  }
  const STAGES = [
    { name: 'Plan', items: [{ agent: 'a', prompt: 'x' }] },
    { name: 'Sign', kind: 'approval', message: 'ok?', after: [0] },
    { name: 'Build', items: [{ agent: 'a', prompt: 'x' }], after: [0] },
  ]

  it('a reserve hold is not the spending limit and does not hide another branch\'s approval alert', async () => {
    api.getWorkflowRun.mockImplementation(async () => ({
      run: run({ stages: STAGES, status: 'paused', current_stage: 1 }),
      tasks: [t('a', 0, 'completed')],
      stage_state: {
        0: { status: 'done' },
        1: { status: 'waiting_approval' },
        2: { status: 'budget', budget_reserve: true, status_word: 'Waiting for other steps', reason: 'Waiting for other steps to finish.' },
      },
      budget: { spent: 1.5, budget_usd: 3 },
    }))
    const c = await mount()
    expect(c.querySelector('[data-testid="run-budget-panel"]')).toBeNull()
    expect(c.textContent).not.toContain('reached its spending limit')
    expect(c.querySelector('[data-testid="run-paused"]').textContent).toContain('approve')
  })

  it('a real spending limit still shows the panel', async () => {
    api.getWorkflowRun.mockImplementation(async () => ({
      run: run({ stages: STAGES, status: 'paused', current_stage: 2 }),
      tasks: [t('a', 0, 'completed')],
      stage_state: { 0: { status: 'done' }, 2: { status: 'budget', reason: 'limit' } },
      budget: { spent: 3, budget_usd: 3 },
    }))
    const c = await mount()
    expect(c.querySelector('[data-testid="run-budget-panel"]')).not.toBeNull()
    expect(c.querySelector('[data-testid="run-paused"]')).toBeNull()
  })

  it('a stopped check looks stopped (grey, with its reason) and the failed run opens on the real failure', async () => {
    const stages = [
      { name: 'Build', items: [{ agent: 'a', prompt: 'x' }] },
      { name: 'Test', kind: 'gate', run: ['pytest'], project: 'p1', after: [0] },
      { name: 'Docs', items: [{ agent: 'a', prompt: 'x' }], after: [0] },
    ]
    api.getWorkflowRun.mockImplementation(async () => ({
      run: run({ stages, status: 'failed', current_stage: 1 }),
      tasks: [t('a', 0, 'completed'), t('d', 2, 'failed')],
      stage_state: {
        0: { status: 'done' },
        1: { status: 'stopped', reason: 'Stopped because Docs failed.', status_word: 'Stopped' },
        2: { status: 'failed', reason: '1 task failed' },
      },
    }))
    const c = await mount()
    const boxes = [...c.querySelectorAll('[data-testid="flow-box"]')]
    expect(boxes.map((b) => b.dataset.status)).toEqual(['done', 'stopped', 'failed'])
    expect(boxes.map((b) => b.getAttribute('aria-pressed'))).toEqual(['false', 'false', 'true'])
    await act(async () => boxes[1].click())
    await flush()
    expect(c.querySelector('[data-testid="run-stage-stopped"]').textContent).toBe('Stopped because Docs failed.')
    expect(failedStepIndex(run({ stages, current_stage: 1 }), [], {
      1: { status: 'failed', reason: 'cancelled' },
      2: { status: 'failed', reason: 'x' },
    })).toBe(2)
  })

  it('Run again from here is not offered for a step that never started', async () => {
    // A; B, C and D... D waits for A and B, C starts at once; B fails; D never started
    const stages = [
      { name: 'A', items: [{ agent: 'a', prompt: 'x' }] },
      { name: 'B', items: [{ agent: 'a', prompt: 'x' }], after: [0] },
      { name: 'C', items: [{ agent: 'a', prompt: 'x' }], after: [0] },
      { name: 'D', items: [{ agent: 'a', prompt: 'x' }], after: [0, 1] },
    ]
    api.getWorkflowRun.mockImplementation(async () => ({
      run: run({ stages, status: 'failed', current_stage: 1 }),
      tasks: [t('a', 0, 'completed'), t('b', 1, 'failed'), t('c', 2, 'completed')],
      stage_state: { 0: { status: 'done' }, 1: { status: 'failed' } },
      step_usage: [],
    }))
    const c = await mount()
    const boxes = [...c.querySelectorAll('[data-testid="flow-box"]')]
    expect(c.querySelector('[data-testid="run-rerun-btn"]')).not.toBeNull() // B (failed) is selected
    await act(async () => boxes[3].click())
    await flush()
    expect(boxes[3].getAttribute('aria-pressed')).toBe('true')
    expect(c.querySelector('[data-testid="run-rerun-btn"]')).toBeNull()
    await act(async () => boxes[2].click())
    await flush()
    expect(c.querySelector('[data-testid="run-rerun-btn"]')).not.toBeNull() // C finished
  })
})
