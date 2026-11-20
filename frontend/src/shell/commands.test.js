import { describe, expect, it, vi } from 'vitest'
import { buildCommands, searchCommands } from './commands'

const projects = [
  { id: 1, name: 'Canonizer' },
  { id: 2, name: 'Billing' },
  { id: 3, name: 'Cannon tools' },
]
const tasks = [
  { id: 't1', title: 'Fix login bug' },
  { id: 't2', title: 'Canon docs' },
]
const workflows = [{ id: 'w1', name: 'Feature' }]

function actions() {
  return { navigate: vi.fn(), newTask: vi.fn(), runWorkflow: vi.fn(), toggleNight: vi.fn() }
}

describe('buildCommands', () => {
  it('has "Open approvals" (pending tasks and actions) for everyone', () => {
    const a = actions()
    const cmds = buildCommands({ isAdmin: false, actions: a })
    const hit = searchCommands(cmds, 'open approvals')[0]
    expect(hit.label).toBe('Open approvals')
    hit.run()
    expect(a.navigate).toHaveBeenLastCalledWith({ page: 'approvals' })
    expect(searchCommands(cmds, 'guard')[0].id).toBe('action:approvals')
  })

  it('hides Users/Teams for non-admins', () => {
    const labels = buildCommands({ projects, tasks, workflows, isAdmin: false, actions: actions() }).map((c) => c.label)
    expect(labels).not.toContain('Users')
    expect(labels).not.toContain('Teams')
    expect(labels).toContain('Home')
    expect(labels).toContain('Approvals')
  })

  it('shows Users/Teams for admins', () => {
    const labels = buildCommands({ projects, tasks, workflows, isAdmin: true, actions: actions() }).map((c) => c.label)
    expect(labels).toContain('Users')
    expect(labels).toContain('Teams')
  })

  it('groups commands and gives each a unique id', () => {
    const cmds = buildCommands({ projects, tasks, workflows, isAdmin: true, actions: actions() })
    for (const c of cmds) {
      expect(['Pages', 'Projects', 'Tasks', 'Runs', 'Workflows', 'Actions']).toContain(c.group)
      expect(typeof c.run).toBe('function')
    }
    expect(new Set(cmds.map((c) => c.id)).size).toBe(cmds.length)
  })

  it('wires run() to the actions', () => {
    const a = actions()
    const cmds = buildCommands({ projects, tasks, workflows, isAdmin: false, actions: a })
    cmds.find((c) => c.id === 'page:approvals').run()
    expect(a.navigate).toHaveBeenLastCalledWith({ page: 'approvals' })
    cmds.find((c) => c.group === 'Tasks' && c.label.includes('Fix login')).run()
    expect(a.navigate).toHaveBeenLastCalledWith({ page: 'tasks', id: 't1' })
    cmds.find((c) => c.group === 'Workflows').run()
    expect(a.runWorkflow).toHaveBeenCalledWith(workflows[0])
    cmds.find((c) => c.id === 'action:new-task').run()
    expect(a.newTask).toHaveBeenCalled()
  })

  it('tolerates missing data', () => {
    const cmds = buildCommands({ isAdmin: false, actions: actions() })
    expect(cmds.some((c) => c.group === 'Pages')).toBe(true)
  })
})

describe('searchCommands', () => {
  const cmds = buildCommands({ projects, tasks, workflows, isAdmin: true, actions: actions() })

  it('ranks project "Canonizer" first for "canon"', () => {
    expect(searchCommands(cmds, 'canon')[0].label).toBe('Canonizer')
  })

  it('returns everything (capped at 20) for an empty query', () => {
    expect(searchCommands(cmds, '').length).toBe(Math.min(cmds.length, 20))
  })

  it('caps results at 20', () => {
    const many = buildCommands({
      projects: Array.from({ length: 40 }, (_, i) => ({ id: i, name: `Proj ${i}` })),
      isAdmin: false,
      actions: actions(),
    })
    expect(searchCommands(many, 'proj').length).toBe(20)
  })

  it('finds pages by name', () => {
    expect(searchCommands(cmds, 'usage')[0].id).toBe('page:usage')
  })
})

describe('isPaletteShortcut', () => {
  const ev = (o) => ({ key: 'k', metaKey: false, ctrlKey: false, altKey: false, shiftKey: false, ...o })
  it('is Cmd-K on mac and Ctrl-K elsewhere', async () => {
    const { isPaletteShortcut } = await import('./commands')
    expect(isPaletteShortcut(ev({ metaKey: true }), true)).toBe(true)
    expect(isPaletteShortcut(ev({ ctrlKey: true }), true)).toBe(false)
    expect(isPaletteShortcut(ev({ ctrlKey: true }), false)).toBe(true)
    expect(isPaletteShortcut(ev({ metaKey: true }), false)).toBe(false)
    expect(isPaletteShortcut(ev({ metaKey: true, key: 'K' }), true)).toBe(true)
    expect(isPaletteShortcut(ev({ metaKey: true, shiftKey: true }), true)).toBe(false)
    expect(isPaletteShortcut(ev({ metaKey: true, key: 'j' }), true)).toBe(false)
  })
})

describe('runs and credentials in the palette', () => {
  it('lists runs by name and opens the run page', () => {
    const a = actions()
    const runs = [{ id: 'r1', name: 'Feature flow', status: 'running' }, { id: 'r2', name: 'Release', status: 'completed' }]
    const cmds = buildCommands({ runs, actions: a })
    const r = cmds.filter((c) => c.group === 'Runs')
    expect(r.map((c) => c.label)).toEqual(['Feature flow', 'Release'])
    expect(r[0].keywords).toContain('running')
    r[0].run()
    expect(a.navigate).toHaveBeenCalledWith({ page: 'runs', id: 'r1' })
    expect(searchCommands(cmds, 'feature flow')[0].id).toBe('run:r1')
  })
  it('offers "My credentials" when the action exists', () => {
    const openCredentials = vi.fn()
    const cmds = buildCommands({ actions: { ...actions(), openCredentials } })
    const c = cmds.find((x) => x.id === 'action:credentials')
    expect(c.label).toBe('My credentials')
    c.run()
    expect(openCredentials).toHaveBeenCalled()
    expect(buildCommands({ actions: actions() }).some((x) => x.id === 'action:credentials')).toBe(false)
  })
})
