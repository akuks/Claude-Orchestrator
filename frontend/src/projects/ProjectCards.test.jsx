import { afterEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { render } from '../home/testRender'
import ProjectCards, { projectCardModel } from './ProjectCards'

const p = (id, over = {}) => ({ id, name: `Proj ${id}`, my_role: 'maintainer', task_count: 3, total_cost_usd: 2, budget_usd: null, ...over })
const liveRun = (id, project_ids, states) => ({
  kind: 'run',
  id,
  title: `Run ${id}`,
  project_ids,
  status: 'running',
  created_at: '2026-11-20T10:00:00Z',
  stages: states.map((state, i) => ({ name: `S${i + 1}`, state, task_count: 1 })),
})

describe('projectCardModel', () => {
  it('idle when nothing live touches the project', () => {
    const m = projectCardModel(p('a'), [liveRun('r1', ['b'], ['current'])], [])
    expect(m.comets).toEqual([])
    expect(m.liveCount).toBe(0)
    expect(m.activity).toBeNull()
  })
  it('one comet per card, attention first, with the rest counted', () => {
    const live = [liveRun('r1', ['a'], ['done', 'current']), liveRun('r2', ['a', 'b'], ['failed', 'next'])]
    const m = projectCardModel(p('a'), live, [])
    expect(m.liveCount).toBe(2)
    expect(m.comets).toHaveLength(1)
    expect(m.comets[0].id).toBe('r2')
    expect(m.more).toBe(1)
    expect(m.activity).toBe('Run r2 · S1 · failed')
  })
  it('paused when any of its schedules is blocked', () => {
    const scheds = [
      { project_id: 'a', blocked_reason: null },
      { project_id: 'a', blocked_reason: 'Owner has no Claude credential' },
      { project_id: 'b', blocked_reason: 'x' },
    ]
    expect(projectCardModel(p('a'), [], scheds).paused).toBe(1)
    expect(projectCardModel(p('a'), [], scheds).pausedReason).toBe('Owner has no Claude credential')
    expect(projectCardModel(p('c'), [], scheds).paused).toBe(0)
  })
  it('budget used vs budget, with a level', () => {
    expect(projectCardModel(p('a'), [], []).budgetText).toBe('$2.00')
    expect(projectCardModel(p('a'), [], []).budgetLevel).toBeNull()
    const m = projectCardModel(p('a', { total_cost_usd: 17, budget_usd: 20 }), [], [])
    expect(m.budgetText).toBe('$17.00 / $20.00')
    expect(m.budgetLevel).toBe('high')
    expect(projectCardModel(p('a', { total_cost_usd: 21, budget_usd: 20 }), [], []).budgetLevel).toBe('over')
    expect(projectCardModel(p('a', { total_cost_usd: 1, budget_usd: 20 }), [], []).budgetLevel).toBe('ok')
  })
})

describe('ProjectCards', () => {
  let view
  afterEach(async () => {
    await view?.unmount()
    view = null
  })

  it('renders live, idle and paused cards and opens a project on click', async () => {
    const onOpen = vi.fn()
    const projects = [p('a'), p('b', { my_role: 'contributor' }), p('c')]
    view = await render(
      <ProjectCards
        projects={projects}
        live={[liveRun('r1', ['a'], ['done', 'current', 'next'])]}
        schedules={[{ project_id: 'c', blocked_reason: 'no credential' }]}
        onOpen={onOpen}
        renderActions={() => <button data-testid="act">x</button>}
      />,
    )
    const cards = view.container.querySelectorAll('[data-testid="project-card"]')
    expect(cards).toHaveLength(3)
    expect(cards[0].querySelector('[data-testid="comet-svg"]')).not.toBeNull()
    expect(cards[0].querySelector('[data-testid="project-card-live"]').textContent).toBe('1 live')
    expect(cards[1].querySelector('[data-testid="project-card-idle"]')).not.toBeNull()
    expect(cards[1].textContent).toContain('contributor')
    expect(cards[2].querySelector('[data-testid="project-card-paused"]')).not.toBeNull()

    await act(async () => cards[1].querySelector('.co-pcard-name').click())
    expect(onOpen).toHaveBeenCalledWith(projects[1])
    await act(async () => cards[2].click())
    expect(onOpen).toHaveBeenLastCalledWith(projects[2])
    // footer actions don't open the project
    onOpen.mockClear()
    await act(async () => cards[0].querySelector('[data-testid="act"]').click())
    expect(onOpen).not.toHaveBeenCalled()
  })
})
