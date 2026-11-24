import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { flush, render } from '../home/testRender'
import FlowCanvas from './FlowCanvas'
import { boxKey } from './flow'

beforeAll(() => {
  window.matchMedia ||= () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} })
})

const STEPS = [
  { name: 'Build', items: [{ agent: 'Planner', prompt: 'plan' }, { agent: 'Builder', prompt: 'build' }] },
  { name: 'Tests', kind: 'gate', project: 'p1', run: ['npm test'], fix: { agent: 'ag1', rounds: 1 } },
  { name: 'Sign', kind: 'approval', message: 'ok?' },
]
const AGENTS = [{ id: 'ag1', name: 'Fixer' }]

const click = async (el) => {
  await act(async () => el.dispatchEvent(new MouseEvent('click', { bubbles: true })))
  await flush()
}
const boxes = (c) => [...c.querySelectorAll('[data-testid="flow-box"]')]

describe('FlowCanvas', () => {
  let v
  afterEach(async () => {
    await v?.unmount()
    v = null
  })

  it('draws Start, a box per step and per parallel agent, and Done', async () => {
    v = await render(<FlowCanvas steps={STEPS} agents={AGENTS} onSelect={() => {}} />)
    const c = v.container
    expect(c.querySelector('[data-testid="flow-start"]').textContent).toContain('Start')
    expect(c.querySelector('[data-testid="flow-end"]').textContent).toContain('Done')
    expect([...c.querySelectorAll('[data-testid="flow-step"]')].map((s) => s.dataset.kind)).toEqual(['agent', 'gate', 'approval'])
    const labels = boxes(c).map((b) => b.getAttribute('aria-label'))
    expect(labels).toEqual([
      'Step 1, Agent 1 of 2: Planner',
      'Step 1, Agent 2 of 2: Builder',
      'Step 2, Check: Tests',
      'Step 2, Fixer: Fixer',
      'Step 3, Ask a person: Sign',
    ])
    expect(boxes(c).every((b) => b.tagName === 'BUTTON')).toBe(true)
    // 4 connectors: start → 1 → 2 → 3 → done
    expect(c.querySelectorAll('[data-testid="flow-link"]').length).toBe(4)
    // the parallel step fans out into two arrows
    expect(c.querySelectorAll('[data-testid="flow-link"]')[0].querySelectorAll('polygon').length).toBe(2)
    expect(c.querySelector('[data-testid="flow-plus"]')).toBeNull()
    expect(c.textContent).not.toMatch(/\b(gate|kind|stage|pipeline)\b/i)
  })

  it('shows the fixer branch with its two labels, and selects the fixer', async () => {
    const onSelect = vi.fn()
    v = await render(<FlowCanvas steps={STEPS} agents={AGENTS} onSelect={onSelect} selected={{ step: 1, item: null }} />)
    const branch = v.container.querySelector('[data-testid="flow-fix-branch"]')
    expect(branch.textContent).toContain('if it fails')
    expect(branch.textContent).toContain('then run again')
    expect(branch.textContent).toContain('Fixer: Fixer')
    const check = boxes(v.container).find((b) => b.dataset.kind === 'gate')
    expect(check.getAttribute('aria-pressed')).toBe('true')
    expect(check.className).toContain('co-flow-sel')
    await click(branch.querySelector('button'))
    expect(onSelect).toHaveBeenCalledWith({ step: 1, item: 'fix' })
  })

  it('+ on a connector inserts the chosen kind at that index', async () => {
    const onInsert = vi.fn()
    v = await render(<FlowCanvas mode="edit" steps={STEPS} agents={AGENTS} onSelect={() => {}} onInsert={onInsert} />)
    const plus = [...v.container.querySelectorAll('[data-testid="flow-plus"]')]
    expect(plus.length).toBe(4)
    expect(plus.every((p) => p.getAttribute('aria-label') === 'Add a step here')).toBe(true)
    await click(plus[2])
    const menu = document.body.querySelector('[data-testid="flow-add-menu"]')
    expect([...menu.querySelectorAll('button')].map((b) => b.textContent)).toEqual(['Agents', 'Check', 'Ask a person'])
    await click(menu.querySelector('[data-testid="flow-add-gate"]'))
    expect(onInsert).toHaveBeenCalledWith(2, 'gate')
    await click(plus[0])
    await click([...document.body.querySelectorAll('[data-testid="flow-add-approval"]')].pop())
    expect(onInsert).toHaveBeenLastCalledWith(0, 'approval')
  })

  it('an empty workflow is Start → + → Done with the + pulsing', async () => {
    v = await render(<FlowCanvas mode="edit" steps={[]} onInsert={() => {}} pulseAdd />)
    const plus = v.container.querySelectorAll('[data-testid="flow-plus"]')
    expect(plus.length).toBe(1)
    expect(plus[0].className).toContain('co-flow-plus-pulse')
    expect(boxes(v.container).length).toBe(0)
  })

  it('moves and deletes steps, and adds a parallel agent', async () => {
    const onMove = vi.fn()
    const onRemove = vi.fn()
    const onAddAgent = vi.fn()
    v = await render(
      <FlowCanvas mode="edit" steps={STEPS} agents={AGENTS} onSelect={() => {}} onInsert={() => {}} onMove={onMove} onRemove={onRemove} onAddAgent={onAddAgent} />,
    )
    const c = v.container
    const left = [...c.querySelectorAll('[data-testid="flow-move-left"]')]
    const right = [...c.querySelectorAll('[data-testid="flow-move-right"]')]
    expect(left[0].disabled).toBe(true)
    expect(right[2].disabled).toBe(true)
    expect(left[1].getAttribute('aria-label')).toBe('Move step 2 left')
    await click(right[0])
    expect(onMove).toHaveBeenCalledWith(0, 1)
    await click(left[2])
    expect(onMove).toHaveBeenLastCalledWith(2, -1)
    await click(c.querySelectorAll('[data-testid="flow-remove"]')[1])
    const ok = [...document.body.querySelectorAll('.ant-popconfirm .ant-btn')].find((b) => b.textContent === 'Delete')
    await click(ok)
    expect(onRemove).toHaveBeenCalledWith(1)
    // "+ agent" only under agents steps
    const chips = c.querySelectorAll('[data-testid="flow-add-agent"]')
    expect(chips.length).toBe(1)
    await click(chips[0])
    expect(onAddAgent).toHaveBeenCalledWith(0)
  })

  it('view and run modes have no editing controls', async () => {
    for (const mode of ['view', 'run']) {
      const w = await render(<FlowCanvas mode={mode} steps={STEPS} onInsert={() => {}} onMove={() => {}} onRemove={() => {}} onAddAgent={() => {}} />)
      expect(w.container.querySelector('[data-testid="flow-plus"]')).toBeNull()
      expect(w.container.querySelector('[data-testid="flow-move-left"]')).toBeNull()
      expect(w.container.querySelector('[data-testid="flow-add-agent"]')).toBeNull()
      await w.unmount()
    }
  })

  it('run statuses set the class, data and label; lines up to reached steps are coloured', async () => {
    const st = { 0: ['done', 'running'], 1: 'failed', fix: 'running', 2: 'next' }
    const statusOf = (i, item) => {
      if (item === 'fix') return st.fix
      if (i === 0) return item == null ? 'running' : st[0][item]
      return st[i]
    }
    v = await render(<FlowCanvas mode="run" steps={STEPS} agents={AGENTS} statusOf={statusOf} onSelect={() => {}} selected={{ step: 0 }} />)
    const b = boxes(v.container)
    expect(b.map((x) => x.dataset.status)).toEqual(['done', 'running', 'failed', 'running', 'next'])
    expect(b[1].className).toContain('co-flow-st-running')
    expect(b[1].getAttribute('aria-label')).toBe('Step 1, Agent 2 of 2: Builder, running')
    expect(b[2].getAttribute('aria-label')).toBe('Step 2, Check: Tests, failed')
    expect(b[4].getAttribute('aria-label')).toBe('Step 3, Ask a person: Sign, not started')
    // no item in the selection: every agent of the step is selected
    expect(b.slice(0, 2).every((x) => x.getAttribute('aria-pressed') === 'true')).toBe(true)
    const links = [...v.container.querySelectorAll('[data-testid="flow-link"]')].map((l) => l.className)
    expect(links[0]).toContain('reached')
    expect(links[1]).toContain('reached')
    expect(links[2]).toContain('muted')
    expect(links[3]).toContain('muted')
  })

  it('marks boxes with errors', async () => {
    v = await render(<FlowCanvas mode="edit" steps={STEPS} onSelect={() => {}} errors={new Set([boxKey(0, 1), boxKey(2, null)])} />)
    const b = boxes(v.container)
    expect(b.filter((x) => x.className.includes('co-flow-err')).map((x) => x.getAttribute('aria-label'))).toEqual([
      'Step 1, Agent 2 of 2: Builder, needs attention',
      'Step 3, Ask a person: Sign, needs attention',
    ])
  })

  it('mini: same shapes, names only, no badges, sub-lines or fixer; boxes are not buttons without onSelect', async () => {
    v = await render(<FlowCanvas size="mini" steps={[{ name: 'A', auto_retry: true, items: [{ agent: 'X', prompt: 'p', outputs: ['o'] }] }, ...STEPS.slice(1)]} />)
    const c = v.container
    expect(boxes(c).map((b) => b.textContent)).toEqual(['X', 'Tests', 'Sign'])
    expect(c.querySelector('.co-flow-badge')).toBeNull()
    expect(c.querySelector('[data-testid="flow-fix-branch"]')).toBeNull()
    expect(c.querySelector('button')).toBeNull()
  })

  it('a run input shows on the Start pill', async () => {
    v = await render(<FlowCanvas mode="run" steps={STEPS} input="PR 12" />)
    expect(v.container.querySelector('[aria-label="Start, running on: PR 12"]')).not.toBeNull()
  })

  it('badges explain themselves in a tooltip and to screen readers', async () => {
    v = await render(
      <FlowCanvas
        steps={[
          { name: 'A', auto_retry: true, items: [{ agent: 'X', prompt: 'p', outputs: ['summary'] }] },
          { name: 'T', kind: 'gate', run: ['npm test'], retry: 'npm test -- --lf' },
        ]}
        onSelect={() => {}}
      />,
    )
    const badges = [...v.container.querySelectorAll('[data-testid="flow-badge"]')]
    expect(badges.map((b) => b.getAttribute('aria-label'))).toEqual([
      'Tries once more if it fails',
      'Hands over 1 thing: summary',
      'Re-runs only the failures first',
    ])
    const b = boxes(v.container)
    expect(b[0].getAttribute('aria-label')).toBe('Step 1, Agent: X, tries once more if it fails, hands over 1 thing: summary')
    await act(async () => badges[1].dispatchEvent(new MouseEvent('mouseover', { bubbles: true })))
    await new Promise((r) => setTimeout(r, 200))
    await flush()
    expect(document.body.querySelector('.ant-tooltip')?.textContent).toContain('Hands over 1 thing: summary')
  })

  it('the + choices open below the line with an arrow; center and mini are classes', async () => {
    v = await render(<FlowCanvas mode="edit" center steps={STEPS} onSelect={() => {}} onInsert={() => {}} />)
    await click(v.container.querySelector('[data-testid="flow-plus"]'))
    const pop = document.body.querySelector('.co-flow-add-pop')
    expect(pop.className).toContain('ant-popover-placement-bottom')
    expect(pop.querySelector('.ant-popover-arrow')).not.toBeNull()
    expect(v.container.querySelector('[data-testid="flow-canvas"]').className).toContain('co-flow-center')
  })
})
