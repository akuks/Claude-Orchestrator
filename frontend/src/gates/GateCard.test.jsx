import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { flush, render } from '../home/testRender'

vi.mock('../api', () => ({ api: { allowWorkflowGate: vi.fn(), workflowGateLog: vi.fn() } }))
import { api } from '../api'
import GateCard, { gateLead } from './GateCard'

const stage = { name: 'Tests', kind: 'gate', run: ['pytest -q', 'ruff check'], project: 'p1' }
const row = (o = {}) => ({ id: 'g1', stage_index: 1, command: 'pytest -q', kind: 'tests', outcome: 'failed', exit_code: 1, duration_ms: 2500, passed: 3, failed: 1, failing: ['test_a'], has_log: true, ...o })

describe('GateCard', () => {
  let view
  beforeAll(() => {
    window.matchMedia ||= () => ({ matches: true, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} })
  })
  beforeEach(() => vi.clearAllMocks())
  afterEach(async () => {
    await view?.unmount()
    view = null
  })
  const mount = async (props) => {
    view = await render(<GateCard runId="r1" index={1} stage={stage} onChanged={() => {}} {...props} />)
    return view.container
  }

  it('running with no live handle says not yet passed', async () => {
    const c = await mount({ state: { status: 'running', passed_cmds: [0] }, live: null })
    expect(c.querySelector('[data-testid="gate-words"]').textContent).toBe('not yet passed (1 of 2 commands passed, none running)')
  })
  it('live shows the command and progress line', async () => {
    const c = await mount({ state: { status: 'running', passed_cmds: [] }, live: { stage_index: 1, command: 'pytest -q', k: 0, n: 2, progress_line: '40 passed' } })
    expect(c.querySelector('[data-testid="gate-words"]').textContent).toBe('running `pytest -q` (0 of 2 commands passed)')
    expect(c.querySelector('[data-testid="gate-progress"]').textContent).toBe('40 passed')
  })
  it('shows a flaky tag', async () => {
    const c = await mount({ state: { status: 'passed' }, gateRuns: [row({ outcome: 'passed', exit_code: 0, flaky: ['test_a'] })] })
    expect(c.querySelector('[data-testid="gate-flaky"]').textContent).toContain('a flaky test is not a fixed test')
  })
  it('says failed then passed unchanged for an empty flaky list', async () => {
    const c = await mount({ state: { status: 'passed' }, gateRuns: [row({ outcome: 'passed', exit_code: 0, flaky: [] })] })
    expect(c.querySelector('[data-testid="gate-flaky-unchanged"]').textContent).toContain('failed then passed unchanged')
    const d = await mount({ state: { status: 'passed' }, gateRuns: [row({ outcome: 'passed', exit_code: 0, flaky: null })] })
    expect(d.querySelector('[data-testid="gate-flaky-unchanged"]')).toBeNull()
  })
  it('Show log fetches and renders lines as text', async () => {
    api.workflowGateLog.mockResolvedValue({ lines: ['hello', '<b>x</b>'], truncated: false })
    const c = await mount({ state: { status: 'failed' }, gateRuns: [row()] })
    await act(async () => c.querySelector('[data-testid="gate-show-log"]').click())
    await flush()
    expect(api.workflowGateLog).toHaveBeenCalledWith('r1', 'g1')
    const pre = c.querySelector('pre[data-testid="gate-log"]')
    expect(pre.textContent).toBe('hello\n<b>x</b>')
    expect(pre.querySelector('b')).toBeNull()
  })
  it('Allow and run calls the api', async () => {
    api.allowWorkflowGate.mockResolvedValue({})
    const onChanged = vi.fn()
    const c = await mount({ state: { status: 'needs_approval', hits: ['rm -rf'] }, onChanged })
    await act(async () => c.querySelector('[data-testid="gate-allow"]').click())
    await flush()
    expect(api.allowWorkflowGate).toHaveBeenCalledWith('r1', 1)
    expect(onChanged).toHaveBeenCalled()
  })

  it('leads with the pass sentence and the test count', async () => {
    const c = await mount({ state: { status: 'passed', status_text: 'passed' }, gateRuns: [row({ kind: 'full', outcome: 'passed', exit_code: 0, passed: 61, failed: 0, failing: [] })] })
    expect(c.querySelector('[data-testid="gate-lead"]').textContent).toBe('Passed — 61 tests passed')
    expect(c.querySelector('[data-testid="gate-words"]')).toBeNull()
    const d = c.querySelector('[data-testid="gate-details"]')
    expect(d.open).toBe(false)
    expect(d.textContent).toContain('exit code 0')
    expect(d.textContent).toContain('Full run')
    expect(d.textContent).not.toContain('Check Tests')
  })
  it('leads with the fail sentence, then the failing names', async () => {
    const c = await mount({ state: { status: 'failed' }, gateRuns: [row({ passed: 61, failed: 2, failing: ['test_a', 'test_b'] })] })
    expect(c.querySelector('[data-testid="gate-lead"]').textContent).toBe('Failed — 2 of 63 tests failed')
    expect([...c.querySelectorAll('[data-testid="gate-failing"] li')].map((l) => l.textContent)).toEqual(['test_a', 'test_b'])
    expect(c.querySelector('[data-testid="gate-show-log"]').textContent).toBe('Show the last lines of the log')
  })
  it('says it plainly when the counts are unknown', async () => {
    const c = await mount({ state: { status: 'failed', reason: 'exit 2' }, gateRuns: [row({ passed: null, failed: null, failing: [] })] })
    expect(c.querySelector('[data-testid="gate-lead"]').textContent).toBe('Failed — the check did not pass')
    const d = await mount({ state: { status: 'passed' }, gateRuns: [row({ outcome: 'passed', passed: null, failed: null, failing: [] })] })
    expect(d.querySelector('[data-testid="gate-lead"]').textContent).toBe('Passed — the commands succeeded')
    expect(gateLead({ status: 'passed' }, []).text).toBe('Passed — the commands succeeded')
  })
  it('counts a pass from the full run, not the re-run of failures', () => {
    const rows = [row({ kind: 'full', passed: 60, failed: 1 }), row({ kind: 'retry', outcome: 'passed', passed: 1, failed: 0 }), row({ kind: 'full', outcome: 'passed', passed: 61, failed: 0 })]
    expect(gateLead({ status: 'passed' }, rows).text).toBe('Passed — 61 tests passed')
  })

  it('says which command failed and how, in plain words', async () => {
    const c = await mount({ state: { status: 'failed', reason: 'false, exit 1' }, gateRuns: [row({ command: 'false', passed: null, failed: null, failing: [] })] })
    const alert = c.querySelector('[data-testid="gate-blocked"]')
    expect(alert.textContent).toBe('The command false failed (exit code 1)')
    expect(alert.querySelector('code').textContent).toBe('false')
    const d = await mount({ state: { status: 'failed', reason: 'x, timed out after 5 min' }, gateRuns: [row({ command: 'x', outcome: 'timed_out', exit_code: null, passed: null, failed: null, failing: [] })] })
    expect(d.querySelector('[data-testid="gate-blocked"]').textContent).toBe('The command x took too long and was stopped')
    expect(d.querySelector('[data-testid="gate-lead"]').textContent).toBe('Failed — the check did not finish in time')
  })
  it('keeps the server reason for a refusal', async () => {
    const c = await mount({ state: { status: 'failed', reason: 'the guard refused: rm -rf /' }, gateRuns: [] })
    expect(c.querySelector('[data-testid="gate-blocked"]').textContent).toBe('the guard refused: rm -rf /')
  })
  it('a check being fixed leads with the try count and links to the fix task', async () => {
    const onOpenTask = vi.fn()
    const fixStage = { ...stage, fix: { agent: 'ag1', rounds: 3 } }
    const c = await mount({
      stage: fixStage,
      state: { status: 'fixing', fix_round: 2, fix_task: 'fx1', reason: 'pytest -q, exit 1' },
      gateRuns: [row({ command: 'pytest -q' })],
      tasks: [{ id: 'fx1', stage_index: 1, status: 'running', title: '[Tests] Fixer: fix round 2' }],
      onOpenTask,
    })
    expect(c.querySelector('[data-testid="gate-lead"]').textContent).toBe('The check failed. An agent is fixing it (try 2 of 3).')
    expect(c.querySelector('[data-testid="gate-fix-reason"]').textContent).toBe('What failed: The command pytest -q failed (exit code 1)')
    expect(c.querySelector('[data-testid="gate-words"]')).toBeNull()
    await act(async () => c.querySelector('[data-testid="gate-fix-task"]').click())
    expect(onOpenTask).toHaveBeenCalledWith('fx1')
    expect(c.textContent).not.toMatch(/\b(gate|stage|pipeline)\b/i)
  })
  it('reveals hidden characters in the failure reason', async () => {
    const c = await mount({ state: { status: 'fixing', fix_round: 1, reason: 'refused: a\u202Eb' }, gateRuns: [] })
    expect(c.querySelector('[data-testid="gate-fix-reason"]').textContent).toContain('⟦U+202E⟧')
    const d = await mount({ state: { status: 'fixing', fix_round: 1, reason: 'x\u200By, exit 1' }, gateRuns: [row({ command: 'x\u200By' })] })
    expect(d.querySelector('[data-testid="gate-fix-reason"] code').textContent).toContain('⟦U+200B⟧')
  })
  it('no link when the fix task is not visible', async () => {
    const c = await mount({ state: { status: 'fixing', fix_round: 1, fix_task: 'hidden' }, tasks: [], onOpenTask: () => {} })
    expect(c.querySelector('[data-testid="gate-lead"]').textContent).toBe('The check failed. An agent is fixing it (try 1 of 1).')
    expect(c.querySelector('[data-testid="gate-fix-task"]')).toBeNull()
  })
  it('keeps the fix attempts listed after the check passes', async () => {
    const onOpenTask = vi.fn()
    const c = await mount({
      state: { status: 'passed', fix_round: 2, fix_task: 'f2' },
      gateRuns: [row({ outcome: 'passed', exit_code: 0, failed: 0, failing: [] })],
      tasks: [
        { id: 'f1', stage_index: 1, status: 'failed', title: '[Tests] Fixer: fix round 1' },
        { id: 'f2', stage_index: 1, status: 'completed', title: '[Tests] Fixer: fix round 2' },
        { id: 'other', stage_index: 0, status: 'completed', title: '[Plan] A: plan' },
      ],
      onOpenTask,
    })
    const rows = [...c.querySelectorAll('[data-testid="gate-fix-attempt"]')]
    expect(c.querySelector('[data-testid="gate-fix-attempts"]').textContent).toContain('Fix attempts')
    expect(rows.map((r) => r.textContent)).toEqual(['Try 1 Did not finishOpen', 'Try 2 DoneOpen'])
    expect(c.querySelector('[data-testid="gate-fix-task"]')).toBeNull()
    await act(async () => rows[0].querySelector('[data-testid="gate-fix-attempt-open"]').click())
    expect(onOpenTask).toHaveBeenCalledWith('f1')
  })
  it('no fix attempts list without fix tasks', async () => {
    const c = await mount({ state: { status: 'passed' }, tasks: [] })
    expect(c.querySelector('[data-testid="gate-fix-attempts"]')).toBeNull()
  })
})

describe('gateLead status words', () => {
  it('uses the server word for a lock wait and an interrupted check, and its own words without one', () => {
    expect(gateLead({ status: 'held', status_word: 'Waiting its turn' }, []).text).toBe('Waiting its turn')
    expect(gateLead({ status: 'running', status_word: 'Interrupted — will continue' }, []).text).toBe('Interrupted — will continue')
    expect(gateLead({ status: 'running', status_word: 'Running' }, []).text).toBe('Running')
    expect(gateLead({ status: 'held' }, []).text).toBe('On hold — waiting until it is ready to run')
    expect(gateLead({ status: 'running' }, []).text).toBe('Running the check…')
  })
})
