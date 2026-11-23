import { useEffect, useMemo, useRef, useState } from 'react'
import {
  Button,
  Descriptions,
  Drawer,
  Empty,
  Input,
  List,
  Popconfirm,
  Space,
  Tag,
  Typography,
  message,
} from 'antd'
import {
  ApartmentOutlined,
  BugOutlined,
  CheckCircleFilled,
  CheckCircleOutlined,
  ClockCircleOutlined,
  CloseCircleOutlined,
  ClockCircleFilled,
  CloseCircleFilled,
  CopyOutlined,
  DeleteOutlined,
  DashboardOutlined,
  DownloadOutlined,
  ExclamationCircleFilled,
  HourglassOutlined,
  InfoCircleOutlined,
  KeyOutlined,
  PlayCircleOutlined,
  PoweroffOutlined,
  PauseCircleOutlined,
  SafetyCertificateOutlined,
  LoadingOutlined,
  MinusCircleFilled,
  ReloadOutlined,
  SendOutlined,
  StopOutlined,
  SyncOutlined,
  WarningOutlined,
} from '@ant-design/icons'
import { api, notifyUnauthorized, PRIORITY_COLORS, STATUS_COLORS, shortModel } from '../api'
import { statusVar } from '../theme/tokens'
import { guardLine, revealHidden, waitingLine } from '../guard/guard'
import '../guard/guard.css'
import HandoffCard from '../resume/HandoffCard'
import ContinueButton from '../resume/ContinueButton'
import RunNotices from '../resume/RunNotices'
import UsageChips from '../usage/UsageChips'
import SoFarLine from '../usage/SoFarLine'
import TokensPanel from '../usage/TokensPanel'
import { ThreadQuestions } from '../questions/QuestionsSection'
import { usageLine, useUsage } from '../usage/usage'
import AskCard from '../asks/AskCard'
import { decisionNoteLine } from '../decisions/decisions'

// co-guard lines in the run log: one icon per kind (guard.js guardLine).
const GUARD_ICON = {
  blocked: <StopOutlined />,
  waiting: <HourglassOutlined />,
  approved: <CheckCircleOutlined />,
  rejected: <CloseCircleOutlined />,
  expired: <ClockCircleOutlined />,
  override: <WarningOutlined />,
  error: <BugOutlined />,
  watchdog: <PoweroffOutlined />,
  ran: <PlayCircleOutlined />,
  granted: <SafetyCertificateOutlined />,
  filed: <PauseCircleOutlined />,
  info: <InfoCircleOutlined />,
}
const GUARD_POLL_MS = 3000

// Plain text only: guard fields come from the agent's commands. Bidi and
// zero-width characters are shown as ⟦U+XXXX⟧ so they cannot reorder the line.
function GuardLine({ line }) {
  return (
    <div className={`co-guard-line co-guard-line-${line.kind}`} data-testid={`guard-line-${line.kind}`}>
      {GUARD_ICON[line.kind] || GUARD_ICON.info}
      <span className="co-guard-line-text">
        {revealHidden(line.text).text}
        {(line.kind === 'waiting' || line.kind === 'filed') && line.canDecide && line.requestId && (
          <>
            {' '}
            <a href={`#/approvals/${encodeURIComponent(line.requestId)}`}>open the request</a>
          </>
        )}
      </span>
    </div>
  )
}

// Token care lines in the run log (usage.js usageLine): budget and fresh-session
// bands, and the automatic fresh Continue. Plain text only.
const USAGE_ICON = { budget80: <DashboardOutlined />, budget95: <DashboardOutlined />, fresh: <SyncOutlined />, auto: <SyncOutlined /> }
function UsageLine({ line }) {
  return (
    <div className={`co-usage-line co-usage-line-${line.kind}`} data-testid={`usage-line-${line.kind}`}>
      {USAGE_ICON[line.kind] || <InfoCircleOutlined />}
      <span className="co-usage-line-text">{line.text}</span>
    </div>
  )
}

const EVENT_STYLE = {
  started: { color: 'var(--co-terminal-muted)', prefix: '▶' },
  system: { color: 'var(--co-terminal-muted)', prefix: '·' },
  text_output: { color: 'var(--co-terminal-text)', prefix: '' },
  tool_use: { color: 'var(--co-terminal-accent)', prefix: '⚙' },
  tool_result: { color: 'var(--co-terminal-dim)', prefix: '↳' },
  log: { color: 'var(--co-terminal-muted)', prefix: '' },
  error: { color: 'var(--co-terminal-error)', prefix: '✖' },
  completed: { color: 'var(--co-terminal-success)', prefix: '✔' },
  decision_note: { color: 'var(--co-terminal-muted)', prefix: '·' },
}

export function renderLine(ev) {
  const s = EVENT_STYLE[ev.type] || { color: 'var(--co-terminal-text)', prefix: '' }
  const p = ev.payload || {}
  let text
  switch (ev.type) {
    case 'text_output':
      text = p.text
      break
    case 'tool_use':
      text = `${p.name}(${p.input ? JSON.stringify(p.input) : ''})`
      break
    case 'tool_result':
      text = p.content
      break
    case 'error':
      text = p.message
      break
    case 'completed':
      text = `completed — ${p.status}${p.num_turns ? `, ${p.num_turns} turns` : ''}${
        p.cost != null ? `, $${p.cost}` : ''
      }`
      break
    case 'started':
      text = `started (${p.model}, max ${p.max_turns} turns)`
      break
    case 'system':
      text = p.subtype || ''
      break
    case 'context_store':
      text = `knowledge base — ${p.docs} doc(s) indexed (~${p.index_approx_tokens} tokens): ${(p.paths || []).join(', ')}`
      break
    case 'decision_note':
      text = decisionNoteLine(p)
      break
    default:
      text = JSON.stringify(p)
  }
  return { color: s.color, prefix: s.prefix, text: text || '' }
}

// Agent name is the part of the title before the first ": ".
function agentOf(t) {
  const i = (t.title || '').indexOf(': ')
  return i > 0 ? t.title.slice(0, i) : null
}
function titleBody(t) {
  const i = (t.title || '').indexOf(': ')
  return i > 0 ? t.title.slice(i + 2) : t.title
}

// Status → icon + colour for the workflow tree.
const STATUS_ICON = {
  completed: { icon: <CheckCircleFilled />, color: statusVar('completed') },
  failed: { icon: <CloseCircleFilled />, color: statusVar('failed') },
  running: { icon: <LoadingOutlined spin />, color: statusVar('running') },
  queued: { icon: <ClockCircleFilled />, color: statusVar('queued') },
  awaiting_approval: { icon: <ExclamationCircleFilled />, color: statusVar('awaiting_approval') },
  cancelled: { icon: <MinusCircleFilled />, color: statusVar('cancelled') },
}
const fmtCost = (c) => (c == null ? null : `$${Number(c).toFixed(2)}`)

// A delegation/workflow tree built from parent_task_id links: coordinator at the
// top, parallel children + follow-ups nested under their parent with proper tree
// connector lines, a status icon, agent chip and clean cost — trackable at a glance.
function DelegationTree({ thread, currentId, onOpenTask }) {
  const GUIDE = 'var(--co-guide)'
  const byParent = {}
  const ids = new Set(thread.map((t) => t.id))
  for (const t of thread) {
    const pid = t.parent_task_id && ids.has(t.parent_task_id) ? t.parent_task_id : '__root__'
    ;(byParent[pid] = byParent[pid] || []).push(t)
  }
  const sortKey = (t) => t.created_at || ''

  const Node = ({ t, isChild }) => {
    const st = STATUS_ICON[t.status] || { icon: <ClockCircleFilled />, color: statusVar('queued') }
    const agent = agentOf(t)
    const active = t.id === currentId
    const kids = (byParent[t.id] || []).sort((a, b) => sortKey(a).localeCompare(sortKey(b)))
    return (
      <div style={{ position: 'relative', paddingLeft: isChild ? 20 : 0 }}>
        {/* horizontal connector from the trunk to this node */}
        {isChild && (
          <span
            style={{
              position: 'absolute',
              left: 0,
              top: 15,
              width: 14,
              height: 1,
              background: GUIDE,
            }}
          />
        )}
        <div
          onClick={() => !active && onOpenTask?.(t.id)}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            padding: '5px 8px',
            borderRadius: 6,
            cursor: active ? 'default' : 'pointer',
            background: active ? 'var(--co-active-bg)' : 'transparent',
            border: active ? '1px solid var(--co-active-border)' : '1px solid transparent',
            transition: 'background 0.12s',
          }}
          onMouseEnter={(e) => {
            if (!active) e.currentTarget.style.background = 'var(--co-hover)'
          }}
          onMouseLeave={(e) => {
            if (!active) e.currentTarget.style.background = 'transparent'
          }}
        >
          <span style={{ color: st.color, fontSize: 15, display: 'inline-flex' }}>{st.icon}</span>
          {agent ? (
            <Tag color="geekblue" style={{ margin: 0, fontWeight: 500 }}>
              {agent}
            </Tag>
          ) : (
            <Tag style={{ margin: 0, color: 'var(--co-text-secondary)' }}>follow-up</Tag>
          )}
          <span
            style={{
              flex: 1,
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
              fontWeight: !isChild ? 600 : 400,
            }}
            title={titleBody(t)}
          >
            {titleBody(t)}
          </span>
          {fmtCost(t.total_cost_usd) && (
            <Typography.Text type="secondary" style={{ fontSize: 11, flexShrink: 0 }}>
              {fmtCost(t.total_cost_usd)}
            </Typography.Text>
          )}
        </div>
        {/* children: vertical trunk + recursive nodes */}
        {kids.length > 0 && (
          <div style={{ marginLeft: isChild ? 20 : 0, borderLeft: `1px solid ${GUIDE}`, paddingLeft: 0 }}>
            {kids.map((k) => (
              <Node key={k.id} t={k} isChild />
            ))}
          </div>
        )}
      </div>
    )
  }

  const roots = (byParent['__root__'] || []).sort((a, b) => sortKey(a).localeCompare(sortKey(b)))
  return (
    <div
      style={{
        border: '1px solid var(--co-border)',
        borderRadius: 8,
        padding: 8,
        background: 'var(--co-surface)',
      }}
    >
      {roots.map((t) => (
        <Node key={t.id} t={t} isChild={false} />
      ))}
    </div>
  )
}

// The task drawer (Tasks page), or with `inline` the same content as a panel
// (embedded on the run page). One component, so live output, follow-ups and
// actions behave the same in both places.
const END_REFRESH_MS = [0, 500, 1000, 2000, 4000]
const TERMINAL_STATUSES = ['completed', 'failed', 'cancelled']

export default function TaskDetail({ taskId, onClose, onChanged, onOpenTask, onOpenCredentials, currentUser, inline = false }) {
  const [task, setTask] = useState(null)
  const [events, setEvents] = useState([])
  const [artifacts, setArtifacts] = useState([])
  const [thread, setThread] = useState([])
  const [followup, setFollowup] = useState('')
  const [sending, setSending] = useState(false)
  const [handoff, setHandoff] = useState(null)
  const [requests, setRequests] = useState([]) // this task's co-guard approval requests
  const [names, setNames] = useState(() => new Map())
  const [endTick, setEndTick] = useState(0) // bumped once a finished run's status is terminal: questions and usage reload
  const wsRef = useRef(null)
  const termRef = useRef(null)
  const lastSeqRef = useRef(0)
  const endTimers = useRef([])
  const endGen = useRef(0)

  const endRefresh = (id, isClosed, setT, finish) => {
    endTimers.current.forEach(clearTimeout)
    endTimers.current = []
    const gen = ++endGen.current
    let stopped = false
    END_REFRESH_MS.forEach((ms, i) => {
      endTimers.current.push(
        setTimeout(async () => {
          if (stopped || gen !== endGen.current || isClosed()) return
          let t = null
          try {
            t = await api.getTask(id)
          } catch (_) {}
          if (stopped || gen !== endGen.current || isClosed()) return
          if (t) setT(t)
          const last = i === END_REFRESH_MS.length - 1
          if ((t && TERMINAL_STATUSES.includes(t.status)) || last) {
            stopped = true
            endTimers.current.forEach(clearTimeout)
            finish()
          }
        }, ms),
      )
    })
  }

  // Load task + open the live stream whenever the selected task changes.
  useEffect(() => {
    if (!taskId) return
    let closed = false
    if (inline) {
      // the panel stays mounted across tasks: don't show the previous one's header
      setTask(null)
      setThread([])
    }
    setEvents([])
    setArtifacts([])
    setFollowup('')
    lastSeqRef.current = 0

    api.getTask(taskId).then((t) => !closed && setTask(t))
    api.getThread(taskId).then((t) => !closed && setThread(t))

    const ws = new WebSocket(api.streamUrl(taskId, 0))
    wsRef.current = ws
    ws.onmessage = (e) => {
      const ev = JSON.parse(e.data)
      if (ev.seq) lastSeqRef.current = Math.max(lastSeqRef.current, ev.seq)
      setEvents((prev) => [...prev, ev])
      if (ev.type === 'completed' || ev.type === 'error') {
        // The worker emits `completed` before it commits the terminal status: refetch until it is terminal.
        endRefresh(taskId, () => closed, setTask, () => {
          api.getThread(taskId).then((t) => !closed && setThread(t))
          api.getArtifacts(taskId).then((a) => !closed && setArtifacts(a))
          setEndTick((n) => n + 1)
          onChanged?.()
        })
      }
    }
    ws.onclose = (e) => {
      if (closed) return
      if (e.code === 4401) notifyUnauthorized()
      else if (e.code === 4403) {
        message.error("Live output blocked: this page's origin isn't in CO_CORS_ORIGINS")
      }
    }
    // Load any already-persisted artifacts (for finished tasks reopened later).
    api.getArtifacts(taskId).then((a) => !closed && setArtifacts(a))

    return () => {
      closed = true
      ws.close()
      endTimers.current.forEach(clearTimeout)
      endTimers.current = []
    }
  }, [taskId])

  // co-guard requests of this task: who decided (run log enrichment) and what
  // still waits for a person. Polled while the task runs and the tab is visible.
  const guardEvents = events.filter((e) => e.type === 'guard').length
  const running = task?.status === 'running'
  useEffect(() => setRequests([]), [taskId])
  useEffect(() => {
    if (!taskId) return
    let live = true
    const load = () =>
      api
        .taskActionApprovals(taskId)
        .then((list) => live && setRequests(Array.isArray(list) ? list : []))
        .catch(() => {})
    load()
    const id = running ? setInterval(() => document.visibilityState !== 'hidden' && load(), GUARD_POLL_MS) : null
    // back on the tab: catch up at once
    const onVisible = () => document.visibilityState !== 'hidden' && load()
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      live = false
      if (id) clearInterval(id)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [taskId, running, guardEvents])
  const needNames = requests.some((a) => a.decided_by)
  useEffect(() => {
    if (!needNames || names.size) return
    api
      .userDirectory()
      .then((us) => setNames(new Map(us.map((u) => [u.id, u.name || u.email]))))
      .catch(() => {})
  }, [needNames, names.size])
  const guardCtx = useMemo(() => ({ approvals: new Map(requests.map((a) => [a.id, a])), names }), [requests, names])
  const waiting = requests.filter((a) => a.status === 'pending')
  const usage = useUsage(taskId, running, endTick)
  const chips = <UsageChips report={usage} />

  // Auto-scroll the terminal as events arrive.
  useEffect(() => {
    if (termRef.current) termRef.current.scrollTop = termRef.current.scrollHeight
  }, [events])

  const act = async (fn, label) => {
    try {
      await fn(taskId)
      message.success(label)
      const t = await api.getTask(taskId)
      setTask(t)
      onChanged?.()
    } catch (e) {
      message.error(e.message)
    }
  }

  const sendFollowup = async () => {
    if (!followup.trim()) return
    setSending(true)
    try {
      const child = await api.followupTask(taskId, followup.trim())
      message.success('Follow-up started')
      setFollowup('')
      onChanged?.()
      onOpenTask?.(child.id) // switch the drawer to the new run in the thread
    } catch (e) {
      message.error(e.message)
    } finally {
      setSending(false)
    }
  }

  const isActive = task && ['queued', 'running', 'awaiting_approval'].includes(task.status)
  const canFollowUp = task && !isActive && task.session_id

  const actions = (
    <Space wrap>
      {isActive && (
        <Button
          danger
          icon={<StopOutlined />}
          onClick={() => act(api.cancelTask, 'Cancelled')}
        >
          Cancel
        </Button>
      )}
      {task && !isActive && (
        <ContinueButton
          task={task}
          user={currentUser}
          thread={thread}
          onContinued={(child) => {
            message.success('Continuing')
            onChanged?.()
            onOpenTask?.(child.id)
          }}
        />
      )}
      {task && !isActive && (
        <Button icon={<ReloadOutlined />} onClick={() => act(api.retryTask, 'Retrying')}>
          Retry
        </Button>
      )}
      <Button icon={<CopyOutlined />} onClick={() => act(api.duplicateTask, 'Duplicated')}>
        Duplicate
      </Button>
      {task && !isActive && (
        <Popconfirm
          title="Delete this task?"
          onConfirm={async () => {
            try {
              await api.deleteTask(taskId)
              message.success('Task deleted')
              onChanged?.()
              onClose?.()
            } catch (e) {
              message.error(e.message)
            }
          }}
        >
          <Button danger icon={<DeleteOutlined />} title="Delete" />
        </Popconfirm>
      )}
    </Space>
  )

  const body = (
    <>
      <SoFarLine thread={usage?.thread} />
      {thread.length > 1 &&
        (() => {
          const ids = new Set(thread.map((t) => t.id))
          const delegated = thread.some(
            (t) => t.parent_task_id && ids.has(t.parent_task_id) && t.id !== taskId
          )
          const done = thread.filter((t) => t.status === 'completed').length
          const failed = thread.filter((t) => t.status === 'failed').length
          const active = thread.filter((t) =>
            ['queued', 'running', 'awaiting_approval'].includes(t.status)
          ).length
          return (
            <div style={{ marginBottom: 12 }}>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                <ApartmentOutlined /> {delegated ? 'Delegated work' : 'Thread'} · {thread.length} tasks
                {' · '}
                {done} done{failed ? `, ${failed} failed` : ''}
                {active ? `, ${active} active` : ''}
              </Typography.Text>
              <div style={{ marginTop: 6 }}>
                {delegated ? (
                  <DelegationTree thread={thread} currentId={taskId} onOpenTask={onOpenTask} />
                ) : (
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                    {thread.map((step, i) => (
                      <Button
                        key={step.id}
                        size="small"
                        type={step.id === taskId ? 'primary' : 'default'}
                        onClick={() => onOpenTask?.(step.id)}
                        title={step.title}
                      >
                        {i + 1}.{' '}
                        <Tag color={STATUS_COLORS[step.status]} style={{ marginInlineStart: 4 }}>
                          {step.status}
                        </Tag>
                      </Button>
                    ))}
                  </div>
                )}
              </div>
            </div>
          )
        })()}

      {task && (
        <Descriptions size="small" column={2} bordered style={{ marginBottom: 16 }}>
          <Descriptions.Item label="Status">
            <Tag color={STATUS_COLORS[task.status]}>{task.status}</Tag>
          </Descriptions.Item>
          <Descriptions.Item label="Priority">
            <Tag color={PRIORITY_COLORS[task.priority]}>{task.priority}</Tag>
          </Descriptions.Item>
          <Descriptions.Item label="Model">
            {shortModel(task.model_used) || task.model || 'default'}
            {task.model_used && task.model && shortModel(task.model_used) !== task.model && (
              <Typography.Text type="secondary"> (requested {task.model})</Typography.Text>
            )}
          </Descriptions.Item>
          <Descriptions.Item label="Project">{task.project || '—'}</Descriptions.Item>
          <Descriptions.Item label="Turns">{task.num_turns ?? '—'}</Descriptions.Item>
          <Descriptions.Item label="Cost">
            {task.total_cost_usd != null ? fmtCost(task.total_cost_usd) : '—'}
          </Descriptions.Item>
          <Descriptions.Item label="Prompt" span={2}>
            <Typography.Text style={{ whiteSpace: 'pre-wrap' }}>{task.prompt}</Typography.Text>
          </Descriptions.Item>
          {task.error && (
            <Descriptions.Item label="Error" span={2}>
              <Typography.Text type="danger" style={{ whiteSpace: 'pre-wrap' }}>
                {task.error}
              </Typography.Text>
              {task.error.startsWith('No Claude credential') && onOpenCredentials && (
                <div style={{ marginTop: 8 }}>
                  <Button size="small" type="primary" icon={<KeyOutlined />} onClick={onOpenCredentials}>
                    Add credentials
                  </Button>
                </div>
              )}
            </Descriptions.Item>
          )}
        </Descriptions>
      )}

      <RunNotices task={task} events={events} handoff={handoff} />
      <ThreadQuestions
        taskId={taskId}
        refreshKey={`${task?.status}:${endTick}`}
        onChanged={onChanged}
        onAnswered={(child) => {
          onChanged?.()
          onOpenTask?.(child.id) // the answer continues the thread in a new step
        }}
      />
      <AskCard taskId={taskId} />
      <HandoffCard taskId={taskId} onHandoff={setHandoff} canManage={task?.can_manage === true} status={task?.status} />

      <Typography.Title level={5}>Live Output</Typography.Title>
      <div
        ref={termRef}
        className="co-terminal"
        style={{
          padding: 12,
          height: 320,
          overflowY: 'auto',
          fontSize: 12,
          lineHeight: 1.5,
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-word',
        }}
      >
        {events.length === 0 && (
          <span className="co-terminal-muted">Waiting for output…</span>
        )}
        {events.map((ev, i) => {
          if (ev.type === 'guard') return <GuardLine key={i} line={guardLine(ev.payload || {}, guardCtx)} />
          const u = usageLine(ev)
          if (u) return <UsageLine key={i} line={u} />
          const line = renderLine(ev)
          return (
            <div key={i} style={{ color: line.color }}>
              {line.prefix ? `${line.prefix} ` : ''}
              {line.text}
            </div>
          )
        })}
        {waiting.map((a) => (
          <GuardLine key={`wait:${a.id}`} line={waitingLine(a)} />
        ))}
      </div>
      <TokensPanel report={usage} />

      <Space
        style={{ marginTop: 16, width: '100%', justifyContent: 'space-between' }}
        align="center"
      >
        <Typography.Title level={5} style={{ margin: 0 }}>
          Result
        </Typography.Title>
        {task?.result_text && (
          <Space size="small">
            <a href={api.reportUrl(taskId, 'pdf')} target="_blank" rel="noreferrer">
              <Button size="small" icon={<DownloadOutlined />}>
                PDF
              </Button>
            </a>
            <a href={api.reportUrl(taskId, 'docx')} target="_blank" rel="noreferrer">
              <Button size="small" icon={<DownloadOutlined />}>
                DOCX
              </Button>
            </a>
          </Space>
        )}
      </Space>
      {task?.result_text ? (
        <div
          style={{
            background: 'var(--co-surface-solid)',
            border: '1px solid var(--co-border)',
            borderRadius: 10,
            padding: 12,
            whiteSpace: 'pre-wrap',
            fontSize: 13,
          }}
        >
          {task.result_text}
        </div>
      ) : (
        <Typography.Text type="secondary">No result yet.</Typography.Text>
      )}

      <Typography.Title level={5} style={{ marginTop: 16 }}>
        Artifacts ({artifacts.length})
      </Typography.Title>
      {artifacts.length ? (
        <List
          size="small"
          dataSource={artifacts}
          renderItem={(a) => (
            <List.Item
              actions={[
                <a
                  key="dl"
                  href={api.artifactUrl(taskId, a.rel_path)}
                  target="_blank"
                  rel="noreferrer"
                >
                  <DownloadOutlined /> download
                </a>,
              ]}
            >
              <List.Item.Meta
                title={a.rel_path}
                description={`${a.size} bytes${a.mime ? ` · ${a.mime}` : ''}`}
              />
            </List.Item>
          )}
        />
      ) : (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="No artifacts" />
      )}

      <Typography.Title level={5} style={{ marginTop: 16 }}>
        Follow up
      </Typography.Title>
      {canFollowUp ? (
        <Space.Compact style={{ width: '100%' }}>
          <Input.TextArea
            autoSize={{ minRows: 1, maxRows: 4 }}
            placeholder="Continue this thread — Claude resumes with full context…"
            value={followup}
            onChange={(e) => setFollowup(e.target.value)}
            onPressEnter={(e) => {
              if (!e.shiftKey) {
                e.preventDefault()
                sendFollowup()
              }
            }}
          />
          <Button
            type="primary"
            icon={<SendOutlined />}
            loading={sending}
            onClick={sendFollowup}
          >
            Send
          </Button>
        </Space.Compact>
      ) : (
        <Typography.Text type="secondary">
          {isActive
            ? 'Available once this run finishes.'
            : 'This task has no resumable session to continue.'}
        </Typography.Text>
      )}
    </>
  )

  if (inline) {
    if (!taskId) return null
    return (
      <section className="co-surface co-task-inline" data-testid="task-detail-inline" aria-label="Task detail">
        <header className="co-task-inline-head">
          <div className="co-usage-head">
            <h3 className="co-task-inline-title">{task ? task.title : 'Task'}</h3>
            {chips}
          </div>
          {actions}
        </header>
        {body}
      </section>
    )
  }

  return (
    <Drawer
      open={!!taskId}
      onClose={onClose}
      width={720}
      title={
        <Space size={8} wrap>
          {task ? task.title : 'Task'}
          {chips}
        </Space>
      }
      extra={actions}
    >
      {body}
    </Drawer>
  )
}
