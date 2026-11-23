import { useCallback, useEffect, useRef, useState } from 'react'
import { Alert, Button, Space, Tag, Typography } from 'antd'
import { api } from '../api'
import DecisionForm from './DecisionForm'
import { KIND_COLORS, proposalOrigin } from './decisions'

// Suggested decisions waiting for a maintainer: accept (through the pre-filled
// form), reject, or leave. Every text is plain text. Renders nothing when empty.
export default function ProposalsQueue({ project, canEdit, onAccepted, onCount }) {
  const pid = project.id
  const [items, setItems] = useState([])
  const [pending, setPending] = useState(0)
  const [notice, setNotice] = useState(null)
  const [busy, setBusy] = useState(false)
  const [accepting, setAccepting] = useState(null)
  const seq = useRef(0)
  const alive = useRef(true)
  const countRef = useRef(onCount)
  countRef.current = onCount
  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
      seq.current++
    }
  }, [])

  const load = useCallback(async () => {
    if (!alive.current) return
    const mine = ++seq.current
    try {
      const r = await api.listProposals(pid, 'pending')
      if (mine !== seq.current) return
      const list = Array.isArray(r?.items) ? r.items : []
      setItems(list)
      const n = Number.isFinite(r?.pending) ? r.pending : list.length
      setPending(n)
      countRef.current?.(n)
    } catch (_) {
      // the queue is optional: keep what is shown
    }
  }, [pid])
  useEffect(() => {
    seq.current++
    setItems([])
    setPending(0)
    setNotice(null)
    setAccepting(null)
    load()
  }, [load])

  const reviewed = async (e) => {
    if (!alive.current) return
    setNotice(e.message)
    setAccepting(null)
    await load()
  }
  const reject = async (p) => {
    setBusy(true)
    try {
      await api.rejectProposal(pid, p.id)
      if (!alive.current) return
      setNotice(null)
      await load()
    } catch (e) {
      if (!alive.current) return
      if (e.status === 409) await reviewed(e)
      else setNotice(e.message)
    } finally {
      if (alive.current) setBusy(false)
    }
  }

  if (pending === 0 && items.length === 0) return null
  return (
    <div data-testid="proposals-queue" style={{ marginBottom: 12 }}>
      <Typography.Text strong>Suggested ({pending})</Typography.Text>
      {notice && <Alert type="warning" showIcon style={{ margin: '6px 0' }} message={notice} data-testid="proposals-notice" />}
      {items.map((p) => (
        <div key={p.id} data-testid={`proposal-${p.id}`} style={{ padding: '8px 0', borderBottom: '1px solid var(--co-border, #f0f0f0)' }}>
          <Space wrap size={6}>
            <Tag color={KIND_COLORS[p.kind]} bordered={false}>{p.kind}</Tag>
            <span>{p.title}</span>
          </Space>
          {p.why && <div><Typography.Text type="secondary">{p.why}</Typography.Text></div>}
          <div>
            {(p.touches || []).map((t) => (
              <code key={t} style={{ marginRight: 6 }}>{t}</code>
            ))}
          </div>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {proposalOrigin(p)}{p.evidence ? ` · ${p.evidence}` : ''}
          </Typography.Text>
          {canEdit && (
            <Space size={4} style={{ display: 'flex', marginTop: 4 }}>
              <Button size="small" type="primary" disabled={busy} onClick={() => setAccepting(p)} data-testid={`proposal-accept-${p.id}`}>Accept</Button>
              <Button size="small" disabled={busy} onClick={() => reject(p)} data-testid={`proposal-reject-${p.id}`}>Reject</Button>
            </Space>
          )}
        </div>
      ))}
      <DecisionForm
        open={Boolean(accepting)}
        projectId={pid}
        proposal={accepting}
        onClose={() => setAccepting(null)}
        onProposalError={reviewed}
        onSaved={async () => {
          if (!alive.current) return
          setNotice(null)
          onAccepted?.()
          await load()
        }}
      />
    </div>
  )
}
