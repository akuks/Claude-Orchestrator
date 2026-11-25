import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Alert, Button, InputNumber, Popconfirm, Skeleton, Tag, message } from 'antd'
import { ReloadOutlined, StopOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import relativeTime from 'dayjs/plugin/relativeTime'
import { api } from '../api'
import { stateLabel } from '../comets/CometSvg'
import { timelineComet } from '../comets/timeline'
import TaskDetail from '../components/TaskDetail'
import { parseTime } from '../home/useHomeData'
import { statusVar } from '../theme/tokens'
import {
  canManageRun,
  defaultTaskFor,
  isRunActive,
  runStages,
  earlierAttemptTasks,
  splitStageTitle,
  stageAttempt,
  tasksOfStage,
} from './runStages'
import ApprovalCard from '../gates/ApprovalCard'
import GateCard from '../gates/GateCard'
import RunLog from '../gates/RunLog'
import { RUN_STATUS_COLOR, runStatusText, stageKind } from '../gates/gates'
import { revealHidden } from '../guard/guard'
import FlowCanvas from '../flow/FlowCanvas'
import { plainDuration, runBoxStatuses, usageLine } from '../flow/flow'
import { plainError } from './plainError'
import './run.css'

dayjs.extend(relativeTime)

const POLL_MS = 3000
const STAGE_TAG = {
  done: 'success',
  current: 'processing',
  next: 'default',
  failed: 'error',
  waiting_approval: 'warning',
}
// A step's state, in the same plain capitalised words as the run status.
const STEP_STATE_TEXT = {
  done: 'Done',
  current: 'Running',
  next: 'Not started',
  failed: 'Failed',
  waiting_approval: 'Waiting for approval',
}

const fmtMin = (m) => (m >= 10 ? Math.round(m).toFixed(0) : (Math.round(m * 10) / 10).toFixed(1))
const fmtTokens = (n) => (n < 1000 ? String(n) : n < 999500 ? `${Math.round(n / 1000)}k` : `${(n / 1e6).toFixed(1)}M`)

// "1 of 2 steps done · 1.6 min", plus cost and tokens only when above zero.
// Falls back to the server's line when the numbers are missing.
export function totalsLine(t) {
  if (!t) return null
  if (t.stages_done == null || t.stages == null) return t.line || null
  const parts = [`${t.stages_done} of ${t.stages} step${t.stages === 1 ? '' : 's'} done`]
  if (t.minutes != null) parts.push(`${fmtMin(Number(t.minutes) || 0)} min`)
  if (Number(t.cost_usd) > 0) parts.push(`$${Number(t.cost_usd).toFixed(2)}`)
  if (Number(t.tokens) > 0) parts.push(`${fmtTokens(Number(t.tokens))} tokens`)
  return parts.join(' · ')
}

const money = (c) => `$${Number(c || 0).toFixed(2)}`
const compact = (n) => (n >= 1000 ? `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}k` : String(n))

// What the agents of a step handed over (their named outputs), text only.
function HandedOver({ tasks }) {
  const withOut = tasks.filter((t) => t.outputs && typeof t.outputs === 'object' && Object.keys(t.outputs).length)
  if (!withOut.length) return null
  return (
    <div className="co-run-handed" data-testid="run-handed-over">
      <div className="co-run-handed-title">Handed over</div>
      {withOut.map((t) => {
        const { agent } = splitStageTitle(t.title)
        return (
          <dl key={t.id} className="co-run-handed-list">
            {withOut.length > 1 && agent && <div className="co-run-handed-agent">{revealHidden(agent).text}</div>}
            {Object.entries(t.outputs).map(([k, v]) => (
              <div key={k} className="co-run-handed-row" data-testid="run-handed-row">
                <dt>{k}</dt>
                <dd>{revealHidden(typeof v === 'string' ? v : JSON.stringify(v)).text}</dd>
              </div>
            ))}
          </dl>
        )
      })}
    </div>
  )
}

// A step held because the run reached its spending limit. Managers can raise
// the limit; the server says why when it refuses (for example a run that ended).
function BudgetPanel({ runId, budget, canRaise, onRaised }) {
  const [value, setValue] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const raise = async () => {
    setBusy(true)
    setError(null)
    try {
      await api.raiseRunBudget(runId, Number(value))
      message.success('Limit raised. The run continues.')
      setValue(null)
      await onRaised?.()
    } catch (e) {
      setError(e?.message || 'Could not raise the limit')
    } finally {
      setBusy(false)
    }
  }
  return (
    <Alert
      className="co-run-paused"
      type="warning"
      showIcon
      data-testid="run-budget-panel"
      message={`This run reached its spending limit (${money(budget?.spent)} of ${money(budget?.budget_usd)}).`}
      description={
        <>
          <div>Nothing already running was stopped. New work waits until the limit is raised.</div>
          {canRaise && (
            <div className="co-run-budget-raise">
              <label>
                <span className="co-run-budget-label">New limit</span>
                <InputNumber
                  prefix="$"
                  min={0.01}
                  max={10000}
                  precision={2}
                  value={value}
                  onChange={setValue}
                  aria-label="New limit"
                  data-testid="run-budget-input"
                />
              </label>
              <Button type="primary" loading={busy} disabled={value == null} onClick={raise} data-testid="run-budget-raise">
                Raise the limit and continue
              </Button>
            </div>
          )}
          {error && <Alert type="error" showIcon message={error} data-testid="run-budget-error" style={{ marginTop: 8 }} />}
        </>
      }
    />
  )
}

function NotFound() {
  return (
    <div className="co-surface co-run-missing" data-testid="run-not-found" role="alert">
      <h1>Run not found or you don't have access</h1>
      <p>It may have been deleted, or it belongs to a project you're not a member of.</p>
      <Button type="primary" href="#/home">
        Go to Home
      </Button>
    </div>
  )
}

// A workflow run's own page (#/runs/<id>): header, the flow canvas of its
// steps with their statuses, the selected stage's tasks, and the selected task's live detail
// (the TaskDetail component, inline).
//
// With `onSelectStage` the selected stage is controlled by the `stage` prop
// (the URL's ?stage=N, so Back/Forward restore it); without, it's local.
export default function RunView({ id, user, projects = [], navigate, onOpenCredentials, stage: routeStage, onSelectStage }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null) // { kind: 'notfound' | 'network', text }
  const [localStage, setLocalStage] = useState(null)
  const controlled = typeof onSelectStage === 'function'
  const stageIdx = controlled ? (routeStage ?? null) : localStage // null → follow the run's head stage
  const [taskId, setTaskId] = useState(null) // null → the stage's default task
  const [people, setPeople] = useState(null)
  const [busy, setBusy] = useState(false)
  const hasData = useRef(false)

  const load = useCallback(async () => {
    try {
      const d = await api.getWorkflowRun(id)
      hasData.current = true
      setData(d)
      setError(null)
    } catch (e) {
      if (e?.status === 404 || e?.status === 403) setError({ kind: 'notfound' })
      // A failed poll keeps the page as it was; only a failed first load shows the error.
      else if (!hasData.current) setError({ kind: 'network', text: e?.message || 'Network error' })
    }
  }, [id])

  useEffect(() => {
    load()
  }, [load])

  const active = isRunActive(data?.run)
  const notFound = error?.kind === 'notfound'
  useEffect(() => {
    if (!active || notFound) return
    const t = setInterval(() => {
      if (document.visibilityState !== 'hidden') load()
    }, POLL_MS)
    return () => clearInterval(t)
  }, [active, notFound, load])

  // Names for "started by" (best effort: the directory is optional here).
  const createdBy = data?.run?.created_by
  useEffect(() => {
    if (!createdBy || createdBy === user?.id || people) return
    let live = true
    api
      .userDirectory()
      .then((u) => live && setPeople(u))
      .catch(() => live && setPeople([]))
    return () => {
      live = false
    }
  }, [createdBy, user?.id, people])

  const run = data?.run
  const tasks = data?.tasks || []
  // Polls return fresh objects; keep the same stages array while nothing
  // changed so the canvas statuses aren't recomputed every poll.
  const sstate = data?.stage_state || run?.stage_state
  const freshStages = useMemo(() => runStages(run, tasks, sstate), [run, tasks, sstate])
  const stagesKey = JSON.stringify(freshStages)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const stages = useMemo(() => freshStages, [stagesKey])
  const head = useMemo(() => timelineComet(stages).headIndex, [stages])
  // Each box's status on the canvas: a step's own, each agent's current task.
  const boxStatus = useMemo(() => runBoxStatuses({ stages: run?.stages, states: stages, sstate, tasks }), [run?.stages, stages, sstate, tasks])
  const statusOf = useCallback(
    (i, item) => {
      const b = boxStatus[i]
      if (!b) return 'next'
      if (item === 'fix') return b.fix || 'next'
      if (typeof item === 'number') return b.items[item] || b.status
      return b.status
    },
    [boxStatus],
  )
  const stepUsage = data?.step_usage
  const usageOf = useCallback((i) => usageLine(Array.isArray(stepUsage) ? stepUsage[i] : null), [stepUsage])
  const wordOf = useCallback((i) => sstate?.[String(i)]?.status_word || null, [sstate])
  const aheadOf = useCallback((i) => !!sstate?.[String(i)]?.ahead && sstate[String(i)].status !== 'approved', [sstate])
  const selStage = stageIdx != null && stageIdx < stages.length ? stageIdx : head >= 0 ? head : null
  const curAttempt = selStage == null ? 0 : stageAttempt(sstate, selStage)
  const stageTasks = selStage == null ? [] : tasksOfStage(tasks, selStage, curAttempt)
  const earlier = selStage == null ? [] : earlierAttemptTasks(tasks, selStage, curAttempt)
  // A check step's tasks are its fix-it tasks: they open only when asked for.
  const selIsAgent = selStage != null && stageKind(run?.stages?.[selStage]) === 'agent'
  const selTask =
    taskId && [...stageTasks, ...earlier].some((t) => t.id === taskId)
      ? taskId
      : selIsAgent
        ? defaultTaskFor(stageTasks)
        : null

  const pickStage = useCallback(
    (i) => {
      if (controlled) onSelectStage(i)
      else setLocalStage(i)
    },
    [controlled, onSelectStage],
  )
  // A box on the canvas: select its step; an agent (or the fixer) also opens its task.
  const selectBox = useCallback(
    ({ step, item }) => {
      pickStage(step)
      const b = boxStatus[step]
      const tid = typeof item === 'number' ? b?.itemTasks?.[item] : item === 'fix' ? b?.fixTask : null
      setTaskId(tid || null)
    },
    [pickStage, boxStatus],
  )
  // Thread steps and follow-ups from the embedded detail: run tasks stay here,
  // anything else (a follow-up thread) opens on the Tasks page.
  const openTask = useCallback(
    (tid) => {
      const hit = tasks.find((t) => t.id === tid)
      if (hit) {
        if (hit.stage_index !== selStage) pickStage(hit.stage_index)
        setTaskId(tid)
      } else navigate?.({ page: 'tasks', id: tid })
    },
    [tasks, navigate, pickStage, selStage],
  )

  const act = async (fn, okText) => {
    setBusy(true)
    try {
      await fn(id)
      message.success(okText)
      await load()
    } catch (e) {
      message.error(plainError(e))
    } finally {
      setBusy(false)
    }
  }

  const rerun = async (i) => {
    setBusy(true)
    try {
      await api.rerunWorkflowStage(id, i)
      message.success('Running again from this step')
      await load()
    } catch (e) {
      message.error(plainError(e))
    } finally {
      setBusy(false)
    }
  }

  if (notFound) return <NotFound />
  if (error?.kind === 'network')
    return (
      <div className="co-run" data-testid="run-error">
        <Alert
          type="error"
          showIcon
          message="Couldn't load this run"
          description={error.text}
          action={
            <Button size="small" icon={<ReloadOutlined />} onClick={load}>
              Retry
            </Button>
          }
        />
      </div>
    )
  if (!run)
    return (
      <div className="co-run" data-testid="run-loading">
        <div className="co-surface co-run-panel">
          <Skeleton active paragraph={{ rows: 4 }} />
        </div>
      </div>
    )

  const projectNames = projects.filter((p) => (run.project_ids || []).includes(p.id)).map((p) => p.name)
  const starter =
    run.created_by && run.created_by === user?.id
      ? 'you'
      : (() => {
          const u = (people || []).find((x) => x.id === run.created_by)
          return u ? u.name || u.email : null
        })()
  const subtitle = [
    projectNames.length ? projectNames.join(', ') : (run.project_ids || []).length ? null : 'No project',
    [
      'started',
      run.created_at ? dayjs(parseTime(run.created_at)).fromNow() : null,
      starter ? `by ${starter}` : null,
    ]
      .filter(Boolean)
      .join(' '),
  ].filter(Boolean)
  const cost = tasks.reduce((a, t) => a + (t.total_cost_usd || 0), 0)
  const tokens = tasks.reduce((a, t) => a + (t.input_tokens || 0) + (t.output_tokens || 0), 0)
  const manage = canManageRun(run, user, projects)
  const totalsText = totalsLine(data?.totals)
  const hasAgentSteps = (run.stages || []).some((st) => stageKind(st) === 'agent')
  const headDef = run.stages?.[run.current_stage]
  const headStatus = sstate?.[String(run.current_stage)]?.status
  const budget = data?.budget
  const hasBudget = budget?.budget_usd != null
  const budgetHeld = active && Object.values(sstate || {}).some((st) => st?.status === 'budget')
  const pausedText =
    stageKind(headDef) === 'approval' && headStatus === 'waiting_approval'
      ? 'Waiting for someone to approve the step below.'
      : stageKind(headDef) === 'gate' && headStatus === 'needs_approval'
        ? 'Waiting for someone to allow the check below to run.'
        : null
  const stage = selStage == null ? null : stages[selStage]
  const stageDef = selStage == null ? null : run.stages[selStage]
  const kind = stageKind(stageDef)
  const isAdmin = user?.role === 'admin' || user?.is_admin
  const canAllow =
    manage &&
    (isAdmin ||
      !stageDef?.project ||
      projects.length === 0 ||
      projects.some((p) => p.id === stageDef.project && p.my_role === 'maintainer'))
  const selWord = selStage == null ? null : sstate?.[String(selStage)]?.status_word || null
  const selRaw = selStage != null && Array.isArray(data?.step_usage) ? data.step_usage[selStage] : null
  const selUsage = usageLine(selRaw)
  const selStats =
    selRaw && (Number(selRaw.input_tokens) > 0 || Number(selRaw.output_tokens) > 0)
      ? `${compact(Number(selRaw.input_tokens) || 0)} tokens in, ${compact(Number(selRaw.output_tokens) || 0)} tokens out · ${plainDuration(selRaw.seconds) || ''}`
      : null
  const futureOpen = run.status === 'running' || run.status === 'paused'
  const canRerun = manage && (run.status === 'completed' || run.status === 'failed') && selStage != null && selStage <= run.current_stage
  const taskButton = (t) => {
    const { agent, text } = splitStageTitle(t.title)
    const on = t.id === selTask
    return (
      <li key={t.id}>
        <button
          type="button"
          className={`co-run-task${on ? ' co-run-task-on' : ''}`}
          aria-pressed={on}
          onClick={() => setTaskId(t.id)}
          data-testid="run-task"
        >
          <span className="co-run-task-dot" style={{ background: statusVar(t.status) }} aria-hidden="true" />
          <span className="co-run-task-main">
            {agent && <span className="co-run-task-agent">{agent}</span>}
            <span className="co-run-task-text">{text}</span>
          </span>
          <span className="co-run-task-meta co-num">
            {t.status.replace('_', ' ')}
            {t.total_cost_usd != null ? ` · ${money(t.total_cost_usd)}` : ''}
          </span>
        </button>
      </li>
    )
  }

  return (
    <div className="co-run" data-testid="run-view">
      <header className="co-run-head">
        <div className="co-run-head-text">
          <div className="co-run-eyebrow">Workflow run</div>
          <h1 data-testid="run-title">
            {run.name}
            <Tag color={RUN_STATUS_COLOR[run.status]} className="co-run-status" data-testid="run-status">
              {runStatusText(run.status)}
            </Tag>
          </h1>
          <div className="co-run-sub" data-testid="run-subtitle">
            {subtitle.join(' · ')}
          </div>
        </div>
        <div className="co-run-head-side">
          <div className="co-run-stats co-num" aria-label="Run totals">
            {totalsText ? (
              <span data-testid="run-totals-line">{totalsText}</span>
            ) : (
              <>
                <span>
                  <b>{money(cost)}</b> so far
                </span>
                {tokens > 0 && (
                  <span>
                    <b>{compact(tokens)}</b> tokens
                  </span>
                )}
              </>
            )}
            {hasBudget && (
              <span data-testid="run-budget-line">
                Spent {money(budget.spent)} of {money(budget.budget_usd)}
              </span>
            )}
            {hasAgentSteps && (
              <span data-testid="run-task-count">
                <b>{tasks.length}</b> task{tasks.length === 1 ? '' : 's'}
              </span>
            )}
          </div>
          {manage && (
            <div className="co-run-actions">
              {run.status === 'failed' && (
                <Button
                  type="primary"
                  icon={<ReloadOutlined />}
                  loading={busy}
                  onClick={() => act(api.retryWorkflowRun, 'Retrying the failed step')}
                  data-testid="run-retry"
                >
                  Retry the failed step
                </Button>
              )}
              {active && (
                <Popconfirm
                  title="Cancel this run and stop its tasks?"
                  onConfirm={() => act(api.cancelWorkflowRun, 'Run cancelled')}
                >
                  <Button danger icon={<StopOutlined />} loading={busy} data-testid="run-cancel">
                    Cancel run
                  </Button>
                </Popconfirm>
              )}
            </div>
          )}
        </div>
      </header>

      {run.input && (
        <p className="co-run-input">
          <span>Running on</span> {run.input}
        </p>
      )}
      {budgetHeld && <BudgetPanel runId={id} budget={budget} canRaise={manage && active} onRaised={load} />}
      {run.status === 'paused' && !budgetHeld && (
        <Alert
          className="co-run-paused"
          type="warning"
          showIcon
          data-testid="run-paused"
          message={
            pausedText || (
              <>
                Waiting on an approval — decide it in <a href="#/approvals">Approvals</a> to continue.
              </>
            )
          }
        />
      )}

      <section className="co-surface co-run-timeline" aria-label="Steps">
        {stages.length ? (
          <FlowCanvas
            mode="run"
            center
            steps={run.stages}
            statusOf={statusOf}
            selected={selStage == null ? null : { step: selStage }}
            onSelect={selectBox}
            usageOf={usageOf}
            wordOf={wordOf}
            aheadOf={aheadOf}
            input={run.input}
            focusStep={head >= 0 ? head : null}
            label="Steps of this run"
          />
        ) : (
          <div className="co-run-empty">This run has no steps.</div>
        )}
      </section>

      {stage && (
        <div className={`co-run-body${selTask ? '' : ' co-run-body-full'}`} data-testid="run-body">
          <section className="co-surface co-run-panel co-run-stage" data-testid="run-stage-tasks" aria-label={`Step ${stage.name}`}>
            <header className="co-run-panel-head">
              <h2>
                <span className="co-run-stage-no">Step {selStage + 1}</span> {stage.name}
              </h2>
              {stageDef?.items?.length > 1 && (
                <span className="co-run-stage-parallel" data-testid="run-stage-parallel" title={stageDef.items.map((it) => it.agent).join(', ')}>
                  {stageDef.items.length} agents at once
                </span>
              )}
              {kind === 'agent' && (
                <Tag color={STAGE_TAG[stage.state]} data-testid="run-stage-state">
                  {selWord || STEP_STATE_TEXT[stage.state] || stateLabel(stage.state)}
                </Tag>
              )}
              {selUsage && (
                <span className="co-run-stage-usage co-num" data-testid="run-stage-usage">
                  {selUsage}
                </span>
              )}
            </header>
            {kind === 'gate' ? (
              <GateCard
                runId={id}
                index={selStage}
                stage={stageDef}
                state={sstate?.[String(selStage)]}
                live={data?.live}
                gateRuns={data?.gate_runs || []}
                canAllow={canAllow}
                onChanged={load}
                tasks={[...earlier, ...stageTasks]}
                onOpenTask={setTaskId}
              />
            ) : kind === 'approval' ? (
              <ApprovalCard runId={id} index={selStage} stage={stageDef} state={sstate?.[String(selStage)]} canDecide={manage} future={futureOpen && selStage > run.current_stage} onChanged={load} />
            ) : stageTasks.length ? (
              <ul className="co-run-tasks">{stageTasks.map(taskButton)}</ul>
            ) : (
              <div className="co-run-empty">
                Not started yet
                {stageDef?.items?.length ? (
                  <div className="co-run-empty-sub">
                    {stageDef.items.length > 1 ? 'Agents working at the same time: ' : 'Agent: '}
                    {stageDef.items.map((it) => it.agent).join(', ')}
                  </div>
                ) : null}
              </div>
            )}
            {canRerun && (
              <div className="co-run-rerun" data-testid="run-rerun">
                <Popconfirm
                  placement="bottomLeft"
                  title={`Run again from ${stage.name}? Later steps start over.`}
                  okText="Run again"
                  cancelText="Not now"
                  onConfirm={() => rerun(selStage)}
                >
                  <Button icon={<ReloadOutlined />} loading={busy} data-testid="run-rerun-btn">
                    Run again from here
                  </Button>
                </Popconfirm>
              </div>
            )}
            {kind === 'agent' && <HandedOver tasks={stageTasks} />}
            {selStats && (
              <details className="co-run-details" data-testid="run-stage-details">
                <summary>Details</summary>
                <div className="co-num">{selStats}</div>
              </details>
            )}
            {kind === 'agent' && earlier.length > 0 && (
              <details className="co-run-earlier" data-testid="run-earlier">
                <summary>Earlier attempts ({earlier.length})</summary>
                <ul className="co-run-tasks">{earlier.map(taskButton)}</ul>
              </details>
            )}
          </section>

          {selTask && (
            <div className="co-run-detail">
              <TaskDetail
                inline
                currentUser={user}
                taskId={selTask}
                onClose={() => setTaskId(null)}
                onChanged={load}
                onOpenTask={openTask}
                onOpenCredentials={onOpenCredentials}
              />
            </div>
          )}
        </div>
      )}
      <RunLog log={data?.log || run.log} />
    </div>
  )
}
