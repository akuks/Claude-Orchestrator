import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Button, Empty, Input, Space, Tag, Tooltip, Typography, message } from 'antd'
import {
  CheckOutlined,
  ClockCircleOutlined,
  CloseOutlined,
  CodeOutlined,
  ReloadOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons'
import dayjs from 'dayjs'
import relativeTime from 'dayjs/plugin/relativeTime'
import { api } from '../api'
import { when } from '../time'
import { closesIn, formatLeft, ruleName } from './guard'
import './guard.css'

dayjs.extend(relativeTime)

// "Actions": tool calls co-guard paused until a person decides (the server
// lists only what the viewer may decide). Live: polled every 3 s while the tab
// is visible, with a per-second countdown to when decisions close. Every
// request field is rendered as plain text — never HTML.

export const POLL_MS = 3000
const LONG_SUMMARY = 300 // chars (or 8 lines) before the block is clamped

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

function Summary({ id, text }) {
  const [open, setOpen] = useState(false)
  const long = text.length > LONG_SUMMARY || text.split('\n').length > 8
  return (
    <div>
      <pre className={`co-guard-cmd${long && !open ? ' co-guard-cmd-clamped' : ''}`} data-testid={`action-summary-${id}`}>
        {text}
      </pre>
      {long && (
        <Button type="link" size="small" className="co-guard-more" onClick={() => setOpen((o) => !o)}>
          {open ? 'Show less' : 'Show all'}
        </Button>
      )}
    </div>
  )
}

function ActionCard({ card, state, now, busy, names, focused, onApprove, onReject }) {
  const [rejecting, setRejecting] = useState(false)
  const [reason, setReason] = useState('')
  const id = card.id
  const left = closesIn(card, now)
  const pending = state === 'pending'
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
          {pending ? (
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
        {(card.rule_ids || []).map((r) => (
          <Tooltip key={r} title={r}>
            <Tag color="orange" bordered={false}>
              {ruleName(r)}
            </Tag>
          </Tooltip>
        ))}
      </div>

      <Summary id={id} text={card.summary || ''} />

      {card.reason && <div className="co-guard-reason">{card.reason}</div>}
      <dl className="co-guard-meta">
        {card.cwd && (
          <>
            <dt>Folder</dt>
            <dd className="co-guard-mono">{card.cwd}</dd>
          </>
        )}
        <dt>Requested by</dt>
        <dd>
          {requester}'s task · {when(card.created_at).fromNow()}
        </dd>
      </dl>

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
            <Space>
              <Button type="primary" icon={<CheckOutlined />} loading={busy} disabled={busy} onClick={() => onApprove(card)}>
                Approve
              </Button>
              <Button danger icon={<CloseOutlined />} disabled={busy} onClick={() => setRejecting(true)}>
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

  const decide = async (card, fn, state) => {
    setBusy(card.id)
    try {
      const out = await fn()
      setOutcomes((o) => ({ ...o, [card.id]: { card: { ...card, ...(out || {}) }, state } }))
      message.success(state === 'approved' ? 'Approved — the call runs once' : 'Rejected')
    } catch (e) {
      const st = conflictState(e)
      if (st) {
        setOutcomes((o) => ({ ...o, [card.id]: { card, state: st } }))
        message.warning(st === 'expired' ? 'This request expired before the decision' : `Already ${st}`)
      } else message.error(e.message)
    } finally {
      if (alive.current) setBusy(null)
    }
    onChangedRef.current?.()
    load()
  }
  const approve = (card) => decide(card, () => api.approveAction(card.id), 'approved')
  const reject = (card, reason) => decide(card, () => api.rejectAction(card.id, reason), 'rejected')

  // Live requests first (in server order), then this session's decisions.
  const shown = useMemo(() => {
    const ids = new Set(items.map((c) => c.id))
    const done = Object.values(outcomes)
      .filter((o) => !ids.has(o.card.id))
      .map((o) => o.card)
    return [...items, ...done]
  }, [items, outcomes])

  const stateOf = (card) => {
    const o = outcomes[card.id]
    if (o) return o.state
    if (card.status && card.status !== 'pending') return card.status
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
        Tool calls a running task wants to make that co-guard paused for a person. Approving lets that one call run
        once; the task waits until you decide or the request expires.
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
