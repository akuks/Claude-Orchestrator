// Pure helpers for the run page. Stage states use the same rules as the
// backend's LiveStageOut (_stage_state in backend/app/routers/workflows.py),
// so a run's stars look the same on Home and on its own page.

const ACTIVE_TASK = new Set(['queued', 'running', 'awaiting_approval'])
const ATTENTION = ['awaiting_approval', 'failed', 'running', 'queued']

// statuses: the stage's (visible) task statuses; index: the stage; current:
// the run's current_stage.
export function stageState(statuses, index, current) {
  const list = Array.isArray(statuses) ? statuses : []
  if (list.length) {
    if (list.every((s) => s === 'completed')) return 'done'
    if (list.some((s) => s === 'failed' || s === 'cancelled')) return 'failed'
    if (list.includes('awaiting_approval')) return 'waiting_approval'
  } else if (index < current) {
    // The run has moved past it; the viewer just can't see its tasks.
    return 'done'
  }
  return index === current ? 'current' : 'next'
}

// [{name, state, task_count}] for the run's stages, from the board's tasks.
// The live endpoint only covers active runs; a completed run's stages are all
// done even when some of their tasks aren't visible to the viewer.
const GATE_STATE = {
  passed: 'done',
  approved: 'done',
  failed: 'failed',
  rejected: 'failed',
  waiting_approval: 'waiting_approval',
  needs_approval: 'waiting_approval',
  running: 'current',
}

// The current attempt of a stage (0 when the run predates attempts).
export const stageAttempt = (sstate, index) => Number(sstate?.[String(index)]?.attempt) || 0

// sstate: the run's stage_state (optional). Only the current attempt's
// tasks count, and gate/approval stages (no tasks) take their state from it.
export function runStages(run, tasks, sstate) {
  if (!run || !Array.isArray(run.stages)) return []
  const byStage = new Map()
  for (const t of Array.isArray(tasks) ? tasks : []) {
    if (t?.stage_index == null) continue
    if ((t.stage_attempt || 0) !== stageAttempt(sstate, t.stage_index)) continue
    if (!byStage.has(t.stage_index)) byStage.set(t.stage_index, [])
    byStage.get(t.stage_index).push(t.status)
  }
  return run.stages.map((st, i) => {
    const statuses = byStage.get(i) || []
    const kind = st && st.kind
    const gs = kind === 'gate' || kind === 'approval' ? sstate?.[String(i)]?.status : null
    let state
    if (gs && GATE_STATE[gs]) state = GATE_STATE[gs]
    else if (run.status === 'completed' && !statuses.length) state = 'done'
    else state = stageState(statuses, i, run.current_stage)
    // Steps side by side: a step working while an earlier one is still open is running too.
    if (state === 'next' && statuses.some((s) => s === 'running' || s === 'queued')) state = 'current'
    return { name: (st && st.name) || `Step ${i + 1}`, state, task_count: statuses.length }
  })
}

export function tasksOfStage(tasks, index, attempt) {
  const all = (Array.isArray(tasks) ? tasks : []).filter((t) => t.stage_index === index)
  return attempt == null ? all : all.filter((t) => (t.stage_attempt || 0) === attempt)
}

// Tasks of earlier attempts of a stage (collapsed in the run view).
export function earlierAttemptTasks(tasks, index, attempt) {
  return (Array.isArray(tasks) ? tasks : []).filter((t) => t.stage_index === index && (t.stage_attempt || 0) !== attempt)
}

// The task to show when a stage is selected: one needing attention (approval,
// failed, running, queued — in that order), else the newest.
export function defaultTaskFor(stageTasks) {
  const list = Array.isArray(stageTasks) ? stageTasks : []
  if (!list.length) return null
  for (const s of ATTENTION) {
    const hit = list.find((t) => t.status === s)
    if (hit) return hit.id
  }
  return list[list.length - 1].id
}

export function isRunActive(run) {
  return ['pending', 'running', 'paused'].includes(run?.status)
}

export function isTaskActive(task) {
  return ACTIVE_TASK.has(task?.status)
}

// Mirrors the backend's cancel/retry rule (_require_run with manage=True):
// the run's creator, an admin, or a maintainer of any project it touches.
export function canManageRun(run, user, projects) {
  if (!run || !user) return false
  if (user.role === 'admin' || (run.created_by && run.created_by === user.id)) return true
  const touched = new Set(run.project_ids || [])
  return (projects || []).some((p) => touched.has(p.id) && p.my_role === 'maintainer')
}

// "[Run name] Agent: what it does" → { agent, text }.
export function splitStageTitle(title) {
  const s = String(title ?? '')
  const m = s.match(/^\[[^\]]*\]\s*([^:]+):\s*(.*)$/s)
  return m ? { agent: m[1].trim(), text: m[2] } : { agent: null, text: s }
}

// The step that really failed (a check failed or rejected, an agent task failed), not
// one only cancelled because another branch failed. -1 when there is none.
export function failedStepIndex(run, tasks, sstate) {
  const list = Array.isArray(run?.stages) ? run.stages : []
  const all = Array.isArray(tasks) ? tasks : []
  for (let i = 0; i < list.length; i++) {
    const own = sstate?.[String(i)]?.status
    if (own === 'failed' || own === 'rejected') return i
    const attempt = stageAttempt(sstate, i)
    if (all.some((t) => t?.stage_index === i && (t.stage_attempt || 0) === attempt && t.status === 'failed')) return i
  }
  return -1
}
