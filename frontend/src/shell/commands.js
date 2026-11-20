import Fuse from 'fuse.js'

// ⌘K command list. Pure data so it can be tested without rendering.

export const NAV_PAGES = [
  { page: 'home', label: 'Home' },
  { page: 'tasks', label: 'Tasks' },
  { page: 'projects', label: 'Projects' },
  { page: 'automation', label: 'Automation' },
  { page: 'approvals', label: 'Approvals' },
  { page: 'usage', label: 'Usage' },
  { page: 'security', label: 'Security' },
  { page: 'mcp', label: 'MCP Servers', keywords: 'mcp' },
  { page: 'users', label: 'Users', adminOnly: true },
  { page: 'teams', label: 'Teams', adminOnly: true },
]

export function visiblePages(isAdmin) {
  return NAV_PAGES.filter((p) => isAdmin || !p.adminOnly)
}

export function buildCommands({ projects = [], tasks = [], runs = [], workflows = [], isAdmin = false, actions = {} } = {}) {
  const { navigate = () => {}, newTask, runWorkflow, toggleNight, openCredentials } = actions
  const cmds = []

  for (const p of visiblePages(isAdmin)) {
    cmds.push({
      id: `page:${p.page}`,
      label: p.label,
      group: 'Pages',
      keywords: p.keywords,
      run: () => navigate({ page: p.page }),
    })
  }
  for (const p of projects || []) {
    cmds.push({
      id: `project:${p.id}`,
      label: p.name,
      group: 'Projects',
      // No project page yet: jump to the task list filtered to it.
      run: () => (actions.openProject ? actions.openProject(p) : navigate({ page: 'projects' })),
    })
  }
  for (const t of tasks || []) {
    cmds.push({
      id: `task:${t.id}`,
      label: t.title || `Task ${t.id}`,
      group: 'Tasks',
      run: () => navigate({ page: 'tasks', id: t.id }),
    })
  }
  for (const r of runs || []) {
    cmds.push({
      id: `run:${r.id}`,
      label: r.name || `Run ${r.id}`,
      group: 'Runs',
      keywords: `run ${r.status || ''}`,
      run: () => navigate({ page: 'runs', id: r.id }),
    })
  }
  for (const w of workflows || []) {
    cmds.push({
      id: `workflow:${w.id}`,
      label: `Run ${w.name}…`,
      group: 'Workflows',
      keywords: w.name,
      run: () => (runWorkflow ? runWorkflow(w) : navigate({ page: 'automation' })),
    })
  }
  cmds.push({
    id: 'action:approvals',
    label: 'Open approvals',
    group: 'Actions',
    keywords: 'pending approve reject actions guard co-guard',
    run: () => navigate({ page: 'approvals' }),
  })
  if (newTask) cmds.push({ id: 'action:new-task', label: 'New task…', group: 'Actions', run: newTask })
  if (openCredentials)
    cmds.push({ id: 'action:credentials', label: 'My credentials', group: 'Actions', keywords: 'claude key token', run: openCredentials })
  if (toggleNight)
    cmds.push({ id: 'action:night', label: 'Toggle night mode', group: 'Actions', keywords: 'dark theme', run: toggleNight })
  return cmds
}

export const MAX_RESULTS = 20

export function searchCommands(commands, query) {
  const q = (query || '').trim()
  if (!q) return commands.slice(0, MAX_RESULTS)
  const fuse = new Fuse(commands, {
    keys: [
      { name: 'label', weight: 0.8 },
      { name: 'keywords', weight: 0.2 },
    ],
    threshold: 0.35,
    ignoreLocation: true,
  })
  return fuse.search(q, { limit: MAX_RESULTS }).map((r) => r.item)
}

export const IS_MAC =
  typeof navigator !== 'undefined' && /Mac|iPhone|iPad|iPod/i.test(navigator.platform || navigator.userAgent || '')

// Cmd-K on mac, Ctrl-K elsewhere (Ctrl-K on mac stays the text-field shortcut).
export function isPaletteShortcut(e, isMac = IS_MAC) {
  if ((e.key || '').toLowerCase() !== 'k' || e.altKey || e.shiftKey) return false
  return isMac ? e.metaKey && !e.ctrlKey : e.ctrlKey && !e.metaKey
}
