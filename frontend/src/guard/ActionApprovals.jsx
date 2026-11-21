import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Alert, Button, Empty, Input, Popconfirm, Space, Tag, Tooltip, Typography, message } from 'antd'
import {
  CheckOutlined,
  ClockCircleOutlined,
  CloseOutlined,
  CodeOutlined,
  EyeInvisibleOutlined,
  PauseCircleOutlined,
  ReloadOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons'
import dayjs from 'dayjs'
import relativeTime from 'dayjs/plugin/relativeTime'
import { api } from '../api'
import { parseTime, when } from '../time'
import {
  ONCE_ONLY_REASON,
  RESUME_BADGE,
  RESUME_HELP,
  canApproveForRun,
  closesIn,
  formatLeft,
  revealHidden,
  ruleName,
  runGrantTooltip,
} from './guard'
import './guard.css'

dayjs.extend(relativeTime)

// "Actions": tool calls co-guard paused until a person decides (the server
// lists only what the viewer may decide). Live: polled every 3 s while the tab
// is visible, with a per-second countdown to when decisions close. Every
// request field is rendered as plain text — never HTML — and the command block
// shows bidi / zero-width characters as visible ⟦U+XXXX⟧ markers.
// Phase 2: Approve once / Approve for this run (run_keys), and the resume
// phase (deny-and-resume: the agent stopped; approving starts a follow-up).

export const POLL_MS = 3000
// Up to this many lines a command is shown in full; longer ones are clamped
// and Approve asks for a confirmation until the person expands them.
export const MAX_FULL_LINES = 20
// The server's cut marker (routers/guard.py _cut), or a trailing "…" (a cut made
// elsewhere): the stored text is not the whole command.
const TRUNCATED = /(… \(truncated, \d+ chars\)|…)\s*$/
export const TRUNCATED_WARNING = "Command too long to show in full — you can't fully review it"

const isHidden = () => typeof document !== 'undefined' && document.visibilityState === 'hidden'

const STATE_TAG = {
  approved: { color: 'success', text: 'Approved' },
  rejected: { color: 'error', text: 'Rejected' },
  expired: { color: 'default', text: 'Expired' },
}

// A 409 from approve/reject → the request's real state, if it says so.
function conflictState(e) {
  if (e?.status !== 409) return null
  const d = String(e.message || '')
  if (d === 'expired') return 'expired'
  const m = d.match(/^already (approved|rejected|expired)/)
  return m ? m[1] : null
}

// Resume phase: the server says so (delivery), or the inline wait of a
// request with resume_until has run out at expires_at (the server moves it on
// its next read). Between the decide margin and expires_at the request is
// still inline: the server answers 409 "giving up" there.
export function inResume(card, now = Date.now()) {
  if (card?.delivery === 'resume') return true
  const exp = parseTime(card?.expires_at)
  return Boolean(card?.resume_until) && card?.status === 'pending' && exp != null && exp <= now
}
function resumeLeft(card, now) {
  const until = card?.resume_until ? Date.parse(card.resume_until) : NaN
  return Number.isFinite(until) ? until - now : 0
}
function formatHours(ms) {
  const m = Math.max(0, Math.floor(ms / 60000))
  return m >= 60 ? `${Math.floor(m / 60)} h ${m % 60} min` : `${m} min`
}

function Summary({ id, text, clamped, open, onToggle }) {
  return (
    <div>
      <pre className={`co-guard-cmd${clamped && !open ? ' co-guard-cmd-clamped' : ''}`} data-testid={`action-summary-${id}`}>
        {revealHidden(text).text}
      </pre>
      {clamped && (
        <Button type="link" size="small" className="co-guard-more" onClick={onToggle}>
          {open ? 'Show less' : 'Show all'}
        </Button>
      )}
    </div>
  )
}

function ActionCard({ card, state, now, busy, names, focused, onApprove, onReject }) {
  const [rejecting, setRejecting] = useState(false)
  const [reason, setReason] = useState('')
  const [expanded, setExpanded] = useState(false)
  const id = card.id
  const text = card.summary || ''
  const truncated = TRUNCATED.test(text)
  const clamped = text.split('\n').length > MAX_FULL_LINES
  const hidden = revealHidden(text).count + revealHidden(card.cwd).count
  // "See what you approve": a cut command, one not expanded, or one with hidden
  // characters needs a second yes.
  const confirmText = truncated
    ? `${TRUNCATED_WARNING}. Approve anyway?`
    : hidden
      ? 'This request contains hidden characters (shown as ⟦U+…⟧). Approve anyway?'
      : clamped && !expanded
        ? "You haven't seen the whole command (it is collapsed). Approve anyway?"
        : null
  const left = closesIn(card, now)
  const pending = state === 'pending'
  const resume = pending && inResume(card, now)
  const runOk = canApproveForRun(card)
  const requester = names.get(card.requested_by) || (card.requested_by ? 'a project member' : 'unknown')
  const tag = STATE_TAG[state]
  return (
    <article
      id={`action-${id}`}
      className={`co-surface co-guard-card${focused ? ' co-guard-card-focus' : ''}${pending ? '' : ' co-guard-card-done'}`}
      data-testid={`action-card-${id}`}
      aria-label={`Action request from ${card.task_title || 'a task'}`}
    >
      <header className="co-guard-card-head">
        <div className="co-guard-card-title">
          <ThunderboltOutlined className="co-guard-card-icon" />
          <a href={`#/tasks/${encodeURIComponent(card.task_id)}`} title="Open the task">
            {card.task_title || 'Task'}
          </a>
          <Tag className="co-guard-tool" bordered={false} icon={<CodeOutlined />}>
            {card.tool || 'tool'}
          </Tag>
        </div>
        <div className="co-guard-card-status">
          {resume ? (
            <Tooltip title={RESUME_HELP}>
              <span className="co-guard-countdown co-num" data-testid={`action-resume-left-${id}`}>
                <ClockCircleOutlined /> open {formatHours(resumeLeft(card, now))}
              </span>
            </Tooltip>
          ) : pending ? (
            <Tooltip title="Decisions close 5 seconds before the request expires">
              <span className="co-guard-countdown co-num" data-testid={`action-countdown-${id}`}>
                <ClockCircleOutlined /> {formatLeft(left)} left
              </span>
            </Tooltip>
          ) : (
            <Tag color={tag?.color} data-testid={`action-state-${id}`}>
              {tag?.text || state}
            </Tag>
          )}
        </div>
      </header>

      <div className="co-guard-rules" data-testid={`action-rules-${id}`}>
        {resume && (
          <Tooltip title={RESUME_HELP}>
            <Tag color="purple" icon={<PauseCircleOutlined />} data-testid={`action-resume-${id}`}>
              {RESUME_BADGE}
            </Tag>
          </Tooltip>
        )}
        {(card.rule_ids || []).map((r) => (
          <Tooltip key={r} title={r}>
            <Tag color="orange" bordered={false}>
              {ruleName(r)}
            </Tag>
          </Tooltip>
        ))}
        {hidden > 0 && (
          <Tooltip title="Bidi or zero-width characters can make text read differently from what runs; each is shown as ⟦U+XXXX⟧">
            <Tag className="co-guard-hidden-chip" icon={<EyeInvisibleOutlined />} data-testid={`action-hidden-${id}`}>
              hidden characters
            </Tag>
          </Tooltip>
        )}
      </div>

      {truncated && (
        <Alert
          className="co-guard-truncated"
          data-testid={`action-truncated-${id}`}
          type="warning"
          showIcon
          message={TRUNCATED_WARNING}
          description="The stored request was cut; what runs may differ from what you can read here."
        />
      )}
      <Summary id={id} text={text} clamped={clamped} open={expanded} onToggle={() => setExpanded((o) => !o)} />

      {card.reason && <div className="co-guard-reason">{card.reason}</div>}
      <dl className="co-guard-meta">
        {card.cwd && (
          <>
            <dt>Folder</dt>
            <dd className="co-guard-mono">{revealHidden(card.cwd).text}</dd>
          </>
        )}
        <dt>Requested by</dt>
        <dd>
          {requester}'s task · {when(card.created_at).fromNow()}
        </dd>
      </dl>
      {pending && !resume && card.resume_until && (
        <Typography.Text type="secondary" className="co-guard-note" data-testid={`action-resume-note-${id}`}>
          If nobody answers in time the agent stops and this request stays open for 24 h; approving later starts a
          follow-up. It is dropped if the thread is cancelled.
        </Typography.Text>
      )}
      {!pending && state === 'approved' && card.scope === 'run' && (
        <Typography.Text type="secondary" className="co-guard-note" data-testid={`action-run-note-${id}`}>
          Approved for this run — {runGrantTooltip(card)}. Revoke it under Run and follow-up grants.
        </Typography.Text>
      )}
      {!pending && state === 'approved' && card.delivery === 'resume' && (
        <Typography.Text className="co-guard-note" data-testid={`action-followup-${id}`}>
          {card.followup_task_id ? (
            <>
              Follow-up started:{' '}
              <a href={`#/tasks/${encodeURIComponent(card.followup_task_id)}`}>open the follow-up</a>
            </>
          ) : (
            "A follow-up starts when the thread's current step ends."
          )}
        </Typography.Text>
      )}

      {pending && card.can_decide && (
        <div className="co-guard-actions">
          {rejecting ? (
            <Space direction="vertical" style={{ width: '100%' }}>
              <Input.TextArea
                rows={2}
                maxLength={2000}
                autoFocus
                placeholder="Reason (optional, shown in the run's log)"
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                data-testid={`action-reject-reason-${id}`}
              />
              <Space>
                <Button danger type="primary" loading={busy} disabled={busy} onClick={() => onReject(card, reason.trim())}>
                  Confirm reject
                </Button>
                <Button disabled={busy} onClick={() => setRejecting(false)}>
                  Cancel
                </Button>
              </Space>
            </Space>
          ) : (
            <Space wrap>
              <Popconfirm
                disabled={!confirmText || busy}
                title={confirmText}
                okText="Approve anyway"
                okButtonProps={{ danger: Boolean(truncated || hidden) }}
                onConfirm={() => onApprove(card, 'once')}
              >
                <Button
                  type="primary"
                  icon={<CheckOutlined />}
                  loading={busy}
                  disabled={busy}
                  onClick={confirmText ? undefined : () => onApprove(card, 'once')}
                >
                  Approve once
                </Button>
              </Popconfirm>
              <Tooltip title={runOk ? runGrantTooltip(card) : ONCE_ONLY_REASON}>
                <span data-testid={`action-run-${id}`}>
                  <Popconfirm
                    disabled={!runOk || !confirmText || busy}
                    title={confirmText}
                    okText="Approve anyway"
                    okButtonProps={{ danger: Boolean(truncated || hidden) }}
                    onConfirm={() => onApprove(card, 'run')}
                  >
                    <Button
                      icon={<CheckOutlined />}
                      disabled={busy || !runOk}
                      onClick={confirmText ? undefined : () => onApprove(card, 'run')}
                    >
                      Approve for this run
                    </Button>
                  </Popconfirm>
                </span>
              </Tooltip>
              <Button danger className="co-guard-reject" icon={<CloseOutlined />} disabled={busy} onClick={() => setRejecting(true)}>
                Reject
              </Button>
            </Space>
          )}
        </div>
      )}
      {pending && !card.can_decide && (
        <Typography.Text type="secondary" className="co-guard-note">
          Another maintainer of the project (or an admin) decides this one.
        </Typography.Text>
      )}
    </article>
  )
}

export default function ActionApprovals({ onChanged, focusId, pollMs = POLL_MS }) {
  const [items, setItems] = useState([])
  const [outcomes, setOutcomes] = useState({}) // id → {card, state}: decided in this session
  const [busy, setBusy] = useState(null)
  const [loaded, setLoaded] = useState(false)
  const [loading, setLoading] = useState(false)
  const [now, setNow] = useState(() => Date.now())
  const [names, setNames] = useState(() => new Map())
  const alive = useRef(true)
  const lastCount = useRef(null)
  const failed = useRef(false)
  const onChangedRef = useRef(onChanged)
  onChangedRef.current = onChanged

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const list = await api.listActionApprovals()
      if (!alive.current) return
      setItems(Array.isArray(list) ? list : [])
      setNow(Date.now())
      if (lastCount.current !== list.length) {
        lastCount.current = list.length
        onChangedRef.current?.()
      }
    } catch (e) {
      // Toast once, not on every 3 s poll while the server is unreachable.
      if (alive.current && !failed.current) message.error(e.message)
      failed.current = true
      return
    } finally {
      if (alive.current) {
        setLoading(false)
        setLoaded(true)
      }
    }
    failed.current = false
  }, [])

  useEffect(() => {
    alive.current = true
    api
      .userDirectory()
      .then((us) => alive.current && setNames(new Map(us.map((u) => [u.id, u.name || u.email]))))
      .catch(() => {})
    return () => {
      alive.current = false
    }
  }, [])

  // Poll while visible; catch up as soon as the tab comes back.
  useEffect(() => {
    load()
    const id = setInterval(() => {
      if (!isHidden()) load()
    }, pollMs)
    const onVisible = () => {
      if (!isHidden()) load()
    }
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      clearInterval(id)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [load, pollMs])

  // Countdown ticks while anything is pending.
  const anyPending = items.some((c) => !outcomes[c.id])
  useEffect(() => {
    if (!anyPending) return
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [anyPending])

  useEffect(() => {
    if (focusId && loaded) document.getElementById(`action-${focusId}`)?.scrollIntoView?.({ block: 'center' })
  }, [focusId, loaded])

  const decide = async (card, fn, state, scope = 'once') => {
    setBusy(card.id)
    const resume = inResume(card)
    try {
      const out = await fn()
      setOutcomes((o) => ({ ...o, [card.id]: { card: { ...card, ...(out || {}) }, state } }))
      message.success(
        state !== 'approved'
          ? 'Rejected'
          : resume
            ? 'Approved — a follow-up step may run it once'
            : scope === 'run'
              ? 'Approved for this run (12 h)'
              : 'Approved — the call runs once',
      )
    } catch (e) {
      if (e?.status === 409) {
        // Any conflict: reload the request and show its real state.
        let fresh = null
        try {
          fresh = await api.getActionApproval(card.id)
        } catch (_) {}
        const st = fresh?.status && fresh.status !== 'pending' ? fresh.status : conflictState(e)
        if (st) {
          setOutcomes((o) => ({ ...o, [card.id]: { card: { ...card, ...(fresh || {}) }, state: st } }))
          message.warning(st === 'expired' ? 'This request expired before the decision' : `Already ${st}`)
        } else message.error(e.message)
      } else message.error(e.message)
    } finally {
      if (alive.current) setBusy(null)
    }
    onChangedRef.current?.()
    load()
  }
  const approve = (card, scope = 'once') => decide(card, () => api.approveAction(card.id, scope), 'approved', scope)
  const reject = (card, reason) => decide(card, () => api.rejectAction(card.id, reason), 'rejected')

  // Live requests first (in server order), then this session's decisions.
  const shown = useMemo(() => {
    const ids = new Set(items.map((c) => c.id))
    const done = Object.values(outcomes)
      .filter((o) => !ids.has(o.card.id))
      .map((o) => o.card)
    // a request decided here shows the server's answer (scope, follow-up) until the poll drops it
    return [...items.map((c) => outcomes[c.id]?.card || c), ...done]
  }, [items, outcomes])

  const stateOf = (card) => {
    const o = outcomes[card.id]
    if (o) return o.state
    if (card.status && card.status !== 'pending') return card.status
    if (inResume(card, now)) return resumeLeft(card, now) > 0 ? 'pending' : 'expired'
    if (card.resume_until) return 'pending' // margin before expires_at: still inline, then the resume phase
    return closesIn(card, now) <= 0 ? 'expired' : 'pending'
  }
  const pendingCount = shown.filter((c) => stateOf(c) === 'pending').length

  return (
    <section className="co-guard-section" data-testid="actions-section" aria-label="Action approvals">
      <Space style={{ marginBottom: 4 }}>
        <Typography.Title level={5} style={{ margin: 0 }}>
          <ThunderboltOutlined /> Actions ({pendingCount})
        </Typography.Title>
        <Button size="small" icon={<ReloadOutlined />} onClick={load} loading={loading && loaded}>
          Refresh
        </Button>
      </Space>
      <Typography.Paragraph type="secondary" className="co-guard-sub">
        Tool calls a running task wants to make that co-guard paused for a person. Approve once lets that one call
        run; Approve for this run also lets the same rules through for the rest of this thread for 12 h. The task
        waits until you decide or the request expires.
      </Typography.Paragraph>
      {loaded && shown.length === 0 ? (
        <Empty
          data-testid="actions-empty"
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description="No actions waiting for you"
        />
      ) : (
        <div className="co-guard-cards">
          {shown.map((card) => (
            <ActionCard
              key={card.id}
              card={card}
              state={stateOf(card)}
              now={now}
              busy={busy === card.id}
              names={names}
              focused={card.id === focusId}
              onApprove={approve}
              onReject={reject}
            />
          ))}
        </div>
      )}
    </section>
  )
}
