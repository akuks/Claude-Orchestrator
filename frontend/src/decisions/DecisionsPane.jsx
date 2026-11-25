import { useCallback, useEffect, useRef, useState } from 'react'
import { Alert, Button, Empty, Modal, Popconfirm, Radio, Select, Space, Tag, Tooltip, Typography, message } from 'antd'
import { PlusOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import relativeTime from 'dayjs/plugin/relativeTime'
import { api } from '../api'
import { when } from '../time'
import DecisionForm from './DecisionForm'
import ProposalsQueue from './ProposalsQueue'
import { INACTIVE_MSG, KINDS, KIND_COLORS, makeRuleDraft } from './decisions'

dayjs.extend(relativeTime)

const day = (s) => (s ? String(s).slice(0, 10) : '')

function problems(item) {
  const out = []
  if (item.touches_unmatched?.length) out.push(`matches no file: ${item.touches_unmatched.join(', ')}`)
  if (item.see_missing) out.push('the file in "see" is missing')
  const c = item.changed_since_verified
  if (c) out.push(`${c.count} commit(s) touched these files since it was verified${c.subjects?.length ? `: ${c.subjects.join('; ')}` : ''}`)
  if (item.unverified_days != null) out.push(`not verified for ${item.unverified_days} days`)
  return out
}

function HealthPanel({ health, canEdit, onVerify }) {
  if (!health || health.available === false) return null
  const items = (health.items || []).filter((i) => problems(i).length)
  if (!health.git_error && !items.length) return null
  return (
    <Alert
      type="warning"
      showIcon
      style={{ marginBottom: 12 }}
      data-testid="decisions-health"
      message="Decision health"
      description={
        <div>
          {health.git_error && <div data-testid="health-git-error">{health.git_error}</div>}
          {items.map((i) => (
            <div key={i.number} data-testid={`health-${i.number}`}>
              <b>{i.display_id}</b> {problems(i).join(' · ')}{' '}
              {canEdit && (
                <Button size="small" type="link" onClick={() => onVerify(i.number)} data-testid={`health-verify-${i.number}`}>
                  Verify
                </Button>
              )}
            </div>
          ))}
        </div>
      }
    />
  )
}

function TrackTags({ track }) {
  if (!track || !(track.shown >= 1)) return null
  const needed = (track.fixed || 0) + (track.failed || 0)
  const k = (track.passed || 0) + (track.completed || 0) + needed
  const tip = (
    <div>
      <div>{`Tasks that were shown this note later needed fixes or failed in ${needed} of ${k} cases. This doesn't mean the note caused it — worth checking it is still right and clear.`}</div>
      {track.last_shown_at && <div style={{ opacity: 0.7 }}>Last shown {when(track.last_shown_at).fromNow()}</div>}
    </div>
  )
  return (
    <>
      <Tooltip title={tip}>
        <Tag bordered={false} data-testid="decision-track">{`Shown ${track.shown}×${needed ? ` · ${needed} needed fixes` : ''}`}</Tag>
      </Tooltip>
      {track.trouble && (
        <Tooltip title={tip}>
          <Tag color="orange" bordered={false} data-testid="decision-trouble">Often followed by trouble</Tag>
        </Tooltip>
      )}
    </>
  )
}

function Row({ pid, d, canEdit, busy, act, onEdit, onRule, active }) {
  const [open, setOpen] = useState(false)
  const [by, setBy] = useState(null)
  const others = active.filter((x) => x.number !== d.number)
  return (
    <div className="co-decision" data-testid={`decision-${d.number}`} style={{ padding: '10px 0', borderBottom: '1px solid var(--co-border, #f0f0f0)' }}>
      <Space wrap size={6}>
        <b>{d.display_id}</b>
        <Tag color={KIND_COLORS[d.kind]} bordered={false}>{d.kind}</Tag>
        {d.status !== 'active' && <Tag data-testid="decision-status">{d.status}{d.superseded_by ? ` by ${`D-${String(d.superseded_by).padStart(3, '0')}`}` : ''}</Tag>}
        <span>{d.title}</span>
        {d.stale_flagged_at && d.status === 'active' && (
          <Tooltip title={d.stale_reason}>
            <Tag color="orange" bordered={false} data-testid="decision-stale">May be out of date</Tag>
          </Tooltip>
        )}
        {d.status === 'active' && <TrackTags track={d.track} />}
      </Space>
      {d.stale_flagged_at && d.status === 'active' && d.stale_reason && (
        <div><Typography.Text type="secondary" style={{ fontSize: 12 }}>{d.stale_reason}</Typography.Text></div>
      )}
      <div>
        {(d.touches || []).map((t) => (
          <code key={t} style={{ marginRight: 6 }}>{t}</code>
        ))}
      </div>
      {d.why && (
        <div>
          <Typography.Text type="secondary" ellipsis={open ? false : true} style={{ display: 'block' }}>{d.why}</Typography.Text>
          <Button size="small" type="link" style={{ padding: 0 }} onClick={() => setOpen(!open)}>{open ? 'Less' : 'More'}</Button>
        </div>
      )}
      {d.see && <div><Typography.Text type="secondary">See: {d.see}</Typography.Text></div>}
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        decided by {d.decided_by_name || 'unknown'} on {day(d.decided_at)} · verified {day(d.last_verified_at)}
      </Typography.Text>
      {canEdit && d.status === 'active' && (
        <Space wrap size={4} style={{ display: 'flex', marginTop: 4 }}>
          <Button size="small" onClick={() => onEdit(d)}>Edit</Button>
          {d.stale_flagged_at ? (
            <Button size="small" disabled={busy} onClick={() => act(() => api.verifyDecision(pid, d.number))} data-testid={`stillvalid-${d.number}`}>Still valid</Button>
          ) : (
            <Button size="small" disabled={busy} onClick={() => act(() => api.verifyDecision(pid, d.number))} data-testid={`verify-${d.number}`}>Still true</Button>
          )}
          <Popconfirm title={`Retire ${d.display_id}?`} okText="Retire" onConfirm={() => act(() => api.retireDecision(pid, d.number))}>
            <Button size="small" disabled={busy} data-testid={`retire-${d.number}`}>Retire</Button>
          </Popconfirm>
          <Select
            size="small"
            style={{ minWidth: 150 }}
            placeholder="Superseded by…"
            value={by}
            data-testid={`supersede-${d.number}`}
            options={others.map((x) => ({ value: x.number, label: `${x.display_id} ${x.title}` }))}
            onChange={(v) => {
              const target = others.find((x) => x.number === v)
              setBy(v)
              Modal.confirm({
                title: `Supersede ${d.display_id}?`,
                content: `${d.display_id} will be marked superseded by ${target?.display_id || v}.`,
                okText: 'Supersede',
                onOk: () => act(() => api.supersedeDecision(pid, d.number, v)),
                afterClose: () => setBy(null),
              })
            }}
          />
          <Button size="small" onClick={() => onRule(d)} data-testid={`rule-${d.number}`}>Make this a rule</Button>
        </Space>
      )}
    </div>
  )
}

// Project drawer tab: recorded decisions, health, and (maintainers) the writes.
export default function DecisionsPane({ project, canEdit, isAdmin = false, onMakeRule, onCount }) {
  const pid = project.id
  const [status, setStatus] = useState('active')
  const [kind, setKind] = useState('')
  const [rows, setRows] = useState([])
  const [pickable, setPickable] = useState([]) // every active decision, whatever the filter (the Supersede picker)
  const [health, setHealth] = useState(null)
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)
  const [healthError, setHealthError] = useState(false)
  const [busy, setBusy] = useState(false)
  const [modal, setModal] = useState(null) // {decision}

  const seq = useRef(0)
  const alive = useRef(true) // false once unmounted: late responses never touch state or refetch
  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
      seq.current++
    }
  }, [])
  // Every async result is checked against the latest request; a late answer for
  // an older project or filter is dropped. Returns the rows, or null if stale/failed.
  const load = useCallback(async () => {
    if (!alive.current) return null
    const mine = ++seq.current
    let list = null
    // a filtered view still offers every active decision as a successor: fetch them unfiltered
    const filtered = status !== 'active' || kind !== ''
    const allActive = filtered ? api.listDecisions(pid, { status: 'active', kind: '' }) : null
    allActive?.catch(() => {})
    try {
      const r = await api.listDecisions(pid, { status, kind })
      if (mine !== seq.current) return null
      list = Array.isArray(r) ? r : []
      let pick = list.filter((x) => x.status === 'active')
      if (allActive) {
        try {
          const a = await allActive
          if (Array.isArray(a)) pick = a.filter((x) => x.status === 'active')
        } catch (_) {
          // keep the filtered list's active entries
        }
        if (mine !== seq.current) return null
      }
      setRows(list)
      setPickable(pick)
      setError(null)
    } catch (e) {
      if (mine !== seq.current) return null
      setError(e.message)
    }
    try {
      const h = await api.decisionsHealth(pid)
      if (mine !== seq.current) return list
      setHealth(h)
      setHealthError(false)
    } catch (_) {
      if (mine !== seq.current) return list
      setHealth(null)
      setHealthError(true)
    }
    return list
  }, [pid, status, kind])
  useEffect(() => {
    // new project: drop the old project's rows, health, notice and open form
    seq.current++
    setRows([])
    setPickable([])
    setHealth(null)
    setHealthError(false)
    setNotice(null)
    setError(null)
    setModal(null)
    setStatus('active')
    setKind('')
  }, [pid])
  useEffect(() => {
    load()
  }, [load])

  const act = async (fn) => {
    setBusy(true)
    try {
      await fn()
      if (!alive.current) return
      setNotice(null)
      await load()
    } catch (e) {
      if (!alive.current) return
      message.error(e.message)
      if (e.status === 409) {
        setNotice(e.message)
        await load()
      }
    } finally {
      if (alive.current) setBusy(false)
    }
  }

  return (
    <div data-testid="decisions-pane">
      <Typography.Paragraph type="secondary">
        Recorded decisions name the files they touch; agents are shown them before they edit those files.
      </Typography.Paragraph>
      <ProposalsQueue key={pid} project={project} canEdit={canEdit} onAccepted={load} onCount={onCount} />
      {healthError && <Typography.Text type="secondary" data-testid="health-unavailable">health check unavailable</Typography.Text>}
      <HealthPanel health={health} canEdit={canEdit} onVerify={(n) => act(() => api.verifyDecision(pid, n))} />
      <Space wrap style={{ marginBottom: 8 }}>
        <Radio.Group
          optionType="button"
          value={status}
          onChange={(e) => setStatus(e.target.value)}
          options={[{ value: 'active', label: 'Active' }, { value: 'all', label: 'All' }]}
          data-testid="decisions-status"
        />
        <Select
          style={{ width: 140 }}
          value={kind}
          onChange={setKind}
          data-testid="decisions-kind"
          options={[{ value: '', label: 'All kinds' }, ...KINDS.map((k) => ({ value: k, label: k }))]}
        />
        {isAdmin && (
          <Popconfirm
            title="Fill the track record from past runs still in the history? Only adds records; nothing is changed or removed."
            okText="Build"
            onConfirm={() =>
              act(async () => {
                const r = await api.backfillDecisionTrack(pid)
                message.success(`Added ${r?.added ?? 0} showings from past runs.`)
              })
            }
          >
            <Button disabled={busy} data-testid="track-backfill">Build from past runs</Button>
          </Popconfirm>
        )}
        {canEdit && (
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setModal({ decision: null })} data-testid="decision-add">
            Add decision
          </Button>
        )}
      </Space>
      {notice && <Alert type="warning" showIcon style={{ marginBottom: 8 }} message={notice} data-testid="decisions-notice" />}
      {error && <Alert type="error" showIcon style={{ marginBottom: 8 }} message={error} />}
      {rows.length === 0 && !error && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="No decisions yet" />}
      {rows.map((d) => (
        <Row
          key={d.number}
          pid={pid}
          d={d}
          canEdit={canEdit}
          busy={busy}
          active={pickable}
          act={act}
          onEdit={(x) => setModal({ decision: rows.find((r) => r.number === x.number) })}
          onRule={(x) => onMakeRule?.(makeRuleDraft(x))}
        />
      ))}
      <DecisionForm
        open={Boolean(modal)}
        projectId={pid}
        decision={modal?.decision || null}
        onClose={() => setModal(null)}
        onSaved={load}
        onConflict={async (m) => {
          if (!alive.current) return
          setNotice(m)
          const gone = m === INACTIVE_MSG
          if (gone) setModal(null) // retired or superseded meanwhile: nothing left to edit
          const list = await load()
          if (!alive.current || gone) return
          // show the fresh entry (new version and fields) in the open form; close it if the entry is gone
          setModal((cur) => {
            if (!cur?.decision || !list) return cur
            const fresh = list.find((r) => r.number === cur.decision.number)
            return fresh && fresh.status === 'active' ? { decision: fresh } : null
          })
        }}
      />
    </div>
  )
}
