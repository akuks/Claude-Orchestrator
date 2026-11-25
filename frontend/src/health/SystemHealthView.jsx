import { useCallback, useEffect, useRef, useState } from 'react'
import { Alert, Button, Popconfirm, Select, Spin, Switch } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import relativeTime from 'dayjs/plugin/relativeTime'
import { api } from '../api'
import { when } from '../time'
import { STATUS_WORD, detailRows, overallLine, overallOf, sortChecks, tidyPreviewLine } from './health'
import './health.css'

dayjs.extend(relativeTime)

const Dot = ({ status, big }) => (
  <span
    className={`co-health-dot co-health-dot-${status}${big ? ' co-health-dot-big' : ''}`}
    role="img"
    aria-label={STATUS_WORD[status] || status}
  />
)

// The re-clone warning's "This is expected" (a maintainer's choice): one line per project.
function RecloneActions({ projects, onDone }) {
  const [busy, setBusy] = useState(null)
  const [error, setError] = useState(null)
  const accept = async (id) => {
    setBusy(id)
    try {
      await api.acceptRootCommit(id)
      setError(null)
      await onDone?.()
    } catch (e) {
      setError(e.message || 'Could not save that')
    } finally {
      setBusy(null)
    }
  }
  return (
    <div data-testid="reclone-actions" style={{ marginTop: 8 }}>
      {projects.map((p) => (
        <div key={p.id} style={{ display: 'flex', gap: 8, alignItems: 'center', justifyContent: 'space-between' }}>
          <span>{p.project}</span>
          <Button size="small" loading={busy === p.id} onClick={() => accept(p.id)} data-testid={`reclone-accept-${p.id}`}>
            This is expected
          </Button>
        </div>
      ))}
      {error && <div className="co-health-summary">{error}</div>}
    </div>
  )
}

export function HealthCard({ check, onChanged }) {
  const rows = detailRows(check.detail)
  const reclone = check.id === 'repo_roots' && Array.isArray(check.detail?.projects) ? check.detail.projects : []
  const hasMore = rows.length > 0 || !!check.fix
  return (
    <div className="co-surface co-health-card" data-testid={`health-${check.id}`} data-status={check.status}>
      <div className="co-health-head">
        <Dot status={check.status} />
        <span>{check.title}</span>
      </div>
      <p className="co-health-summary">{check.summary}</p>
      {reclone.length > 0 && <RecloneActions projects={reclone} onDone={onChanged} />}
      {hasMore && (
        <details className="co-health-details">
          <summary>Details</summary>
          {check.fix && (
            <div className="co-health-fix" data-testid={`health-fix-${check.id}`}>
              What to do: {check.fix}
            </div>
          )}
          {rows.length > 0 && (
            <dl>
              {rows.map(([k, v]) => (
                <div key={k} style={{ display: 'contents' }}>
                  <dt>{k}</dt>
                  <dd>{v}</dd>
                </div>
              ))}
            </dl>
          )}
        </details>
      )}
    </div>
  )
}

// "Run history": the switch for tidying old run history (admins only; this page is admin-only).
export function RunHistoryCard() {
  const [r, setR] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const timer = useRef(null)

  const load = useCallback(async () => {
    try {
      const x = await api.retention()
      setR(x)
      setError(null)
      return x
    } catch (e) {
      setError(e.message || 'Could not load the run history settings')
      return null
    }
  }, [])
  const poll = useCallback(async () => {
    const x = await load()
    if (x?.running) timer.current = setTimeout(poll, 2000)
    else setBusy(false)
  }, [load])
  useEffect(() => {
    poll()
    return () => clearTimeout(timer.current)
  }, [poll])

  const save = async (payload) => {
    try {
      setR(await api.saveRetention(payload))
      setError(null)
    } catch (e) {
      setError(e.message || 'Could not save that')
    }
  }
  const tidyNow = async () => {
    setBusy(true)
    try {
      await api.runRetention()
      poll()
    } catch (e) {
      setError(e.message || 'Could not start tidying')
      setBusy(false)
    }
  }
  const running = busy || !!r?.running
  return (
    <div className="co-surface co-health-card" data-testid="health-run-history">
      <div className="co-health-head">
        <span>Run history</span>
      </div>
      {error && <p className="co-health-summary">{error}</p>}
      {r && (
        <>
          <label style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <Switch
              checked={!!r.enabled}
              onChange={(v) => save({ enabled: v })}
              data-testid="tidy-switch"
              aria-label="Tidy old run history"
            />
            <span>Tidy old run history</span>
          </label>
          <p className="co-health-summary">
            {r.enabled
              ? 'Every day, the full history of tasks finished long ago is replaced by a short summary.'
              : 'Off. Nothing is changed until you switch it on.'}
          </p>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <span>Keep full history for:</span>
            <Select
              size="small"
              value={r.days}
              onChange={(v) => save({ days: v })}
              data-testid="tidy-days"
              options={(r.choices || [30, 60, 90]).map((d) => ({ value: d, label: `${d} days` }))}
              style={{ width: 100 }}
            />
          </div>
          <p className="co-health-summary" data-testid="tidy-preview">
            {tidyPreviewLine(r)}
          </p>
          <p className="co-health-summary" style={{ fontSize: 12, opacity: 0.7 }} data-testid="tidy-note">
            The database reuses this space; the disk file doesn't shrink.
          </p>
          {r.last_run && (
            <p className="co-health-summary" data-testid="tidy-last">
              Last tidied {when(r.last_run).format('D MMM YYYY')}: {r.last_count ?? 0} {r.last_count === 1 ? 'task' : 'tasks'}
            </p>
          )}
          <Popconfirm
            title={`Tidy now? Older run history of ${r.tasks} finished ${r.tasks === 1 ? 'task' : 'tasks'} will be replaced by short summaries. This can't be undone.`}
            okText="Tidy now"
            cancelText="Cancel"
            onConfirm={tidyNow}
            disabled={!r.enabled || running || !r.tasks}
          >
            <Button disabled={!r.enabled || running || !r.tasks} loading={running} data-testid="tidy-now">
              Tidy now
            </Button>
          </Popconfirm>
        </>
      )}
    </div>
  )
}

export default function SystemHealthView() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [, setTick] = useState(0)

  const load = useCallback(async (refresh) => {
    setLoading(true)
    try {
      setData(await api.systemHealth(refresh))
      setError(null)
    } catch (e) {
      setError(e.message || 'Could not load the health checks')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load(false)
  }, [load])
  useEffect(() => {
    const t = setInterval(() => setTick((n) => n + 1), 30000) // keeps "Last checked" fresh
    return () => clearInterval(t)
  }, [])

  const checks = data?.checks || []
  const status = overallOf(checks)
  return (
    <div data-testid="system-health">
      <div className="co-health-top">
        <div>
          {data && (
            <h2 className="co-health-overall" data-testid="health-overall">
              <Dot status={status} big />
              {overallLine(checks)}
            </h2>
          )}
          {data?.checked_at && (
            <div className="co-health-checked" data-testid="health-checked">
              Last checked {when(data.checked_at).fromNow()}
            </div>
          )}
        </div>
        <div className="co-health-actions">
          <Button icon={<ReloadOutlined />} loading={loading} onClick={() => load(true)} data-testid="health-refresh">
            Check again
          </Button>
        </div>
      </div>
      {error && <Alert type="error" showIcon message={error} style={{ marginBottom: 16 }} />}
      {!data && loading && <Spin />}
      <div className="co-health-grid" data-testid="health-grid">
        {sortChecks(checks).map((c) => (
          <HealthCard key={c.id} check={c} onChanged={() => load(true)} />
        ))}
        <RunHistoryCard />
      </div>
    </div>
  )
}
