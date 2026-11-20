import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Alert, Button, Popconfirm, Skeleton, Tag, message } from 'antd'
import { ReloadOutlined, StopOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import relativeTime from 'dayjs/plugin/relativeTime'
import { api } from '../api'
import Comets3D from '../comets/Comets3D'
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
  splitStageTitle,
  tasksOfStage,
} from './runStages'
import './run.css'

dayjs.extend(relativeTime)

const POLL_MS = 3000
const RUN_TAG = {
  pending: 'default',
  running: 'processing',
  paused: 'warning',
  completed: 'success',
  failed: 'error',
}
const STAGE_TAG = {
  done: 'success',
  current: 'processing',
  next: 'default',
  failed: 'error',
  waiting_approval: 'warning',
}
const money = (c) => `$${Number(c || 0).toFixed(2)}`
const compact = (n) => (n >= 1000 ? `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}k` : String(n))

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

// A workflow run's own page (#/runs/<id>): header, a comet timeline across
// its stages, the selected stage's tasks, and the selected task's live detail
// (the TaskDetail component, inline).
export default function RunView({ id, user, projects = [], navigate, onOpenCredentials }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null) // { kind: 'notfound' | 'network', text }
  const [stageIdx, setStageIdx] = useState(null) // null → follow the run's head stage
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
  const stages = useMemo(() => runStages(run, tasks), [run, tasks])
  const head = useMemo(() => timelineComet(stages).headIndex, [stages])
  const selStage = stageIdx != null && stageIdx < stages.length ? stageIdx : head >= 0 ? head : null
  const stageTasks = selStage == null ? [] : tasksOfStage(tasks, selStage)
  const selTask = taskId && stageTasks.some((t) => t.id === taskId) ? taskId : defaultTaskFor(stageTasks)

  const selectStage = useCallback((_stage, i) => {
    setStageIdx(i)
    setTaskId(null)
  }, [])
  // Thread steps and follow-ups from the embedded detail: run tasks stay here,
  // anything else (a follow-up thread) opens on the Tasks page.
  const openTask = useCallback(
    (tid) => {
      const hit = tasks.find((t) => t.id === tid)
      if (hit) {
        setStageIdx(hit.stage_index)
        setTaskId(tid)
      } else navigate?.({ page: 'tasks', id: tid })
    },
    [tasks, navigate],
  )

  const act = async (fn, okText) => {
    setBusy(true)
    try {
      await fn(id)
      message.success(okText)
      await load()
    } catch (e) {
      message.error(e.message)
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
  const stage = selStage == null ? null : stages[selStage]
  const stageDef = selStage == null ? null : run.stages[selStage]

  return (
    <div className="co-run" data-testid="run-view">
      <header className="co-run-head">
        <div className="co-run-head-text">
          <div className="co-run-eyebrow">Workflow run</div>
          <h1 data-testid="run-title">
            {run.name}
            <Tag color={RUN_TAG[run.status]} className="co-run-status">
              {run.status}
            </Tag>
          </h1>
          <div className="co-run-sub" data-testid="run-subtitle">
            {subtitle.join(' · ')}
          </div>
        </div>
        <div className="co-run-head-side">
          <div className="co-run-stats co-num" aria-label="Run totals">
            <span>
              <b>{money(cost)}</b> so far
            </span>
            {tokens > 0 && (
              <span>
                <b>{compact(tokens)}</b> tokens
              </span>
            )}
            <span>
              <b>{tasks.length}</b> task{tasks.length === 1 ? '' : 's'}
            </span>
          </div>
          {manage && (
            <div className="co-run-actions">
              {run.status === 'failed' && (
                <Button
                  type="primary"
                  icon={<ReloadOutlined />}
                  loading={busy}
                  onClick={() => act(api.retryWorkflowRun, 'Retrying the failed stage')}
                  data-testid="run-retry"
                >
                  Retry failed stage
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
      {run.status === 'paused' && (
        <Alert
          className="co-run-paused"
          type="warning"
          showIcon
          message={
            <>
              Waiting on an approval — decide it in <a href="#/approvals">Approvals</a> to continue.
            </>
          }
        />
      )}

      <section className="co-surface co-run-timeline" aria-label="Stages">
        {stages.length ? (
          <Comets3D variant="timeline" stages={stages} selectedStage={selStage} onSelectStage={selectStage} />
        ) : (
          <div className="co-run-empty">This run has no stages.</div>
        )}
      </section>

      {stage && (
        <div className="co-run-body">
          <section className="co-surface co-run-panel co-run-stage" data-testid="run-stage-tasks" aria-label={`Stage ${stage.name}`}>
            <header className="co-run-panel-head">
              <h2>
                <span className="co-run-stage-no">Stage {selStage + 1}</span> {stage.name}
              </h2>
              <Tag color={STAGE_TAG[stage.state]}>{stateLabel(stage.state)}</Tag>
            </header>
            {stageTasks.length ? (
              <ul className="co-run-tasks">
                {stageTasks.map((t) => {
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
                })}
              </ul>
            ) : (
              <div className="co-run-empty">
                Not started yet
                {stageDef?.items?.length ? (
                  <div className="co-run-empty-sub">
                    {stageDef.items.length > 1 ? 'Runs in parallel: ' : 'Runs: '}
                    {stageDef.items.map((it) => it.agent).join(', ')}
                  </div>
                ) : null}
              </div>
            )}
          </section>

          <div className="co-run-detail">
            {selTask ? (
              <TaskDetail
                inline
                taskId={selTask}
                onClose={() => setTaskId(null)}
                onChanged={load}
                onOpenTask={openTask}
                onOpenCredentials={onOpenCredentials}
              />
            ) : (
              <div className="co-surface co-run-panel co-run-empty">Select a task to see its live output.</div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
