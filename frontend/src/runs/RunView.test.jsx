import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { flush, render } from '../home/testRender'

vi.mock('../api', () => ({
  api: {
    getWorkflowRun: vi.fn(),
    userDirectory: vi.fn(),
    cancelWorkflowRun: vi.fn(),
    retryWorkflowRun: vi.fn(),
  },
  STATUS_COLORS: {},
}))
// The embedded TaskDetail opens a WebSocket; a stub keeps these tests about RunView.
vi.mock('../components/TaskDetail', () => ({
  default: ({ taskId }) => <div data-testid="task-detail-stub">{taskId}</div>,
}))

import { api } from '../api'
import RunView from './RunView'
import { canManageRun, defaultTaskFor, runStages, stageState } from './runStages'

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
    expect(runStages(run({ stages: [{}] }), [])[0].name).toBe('Stage 1')
    expect(runStages(null, null)).toEqual([])
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
})
