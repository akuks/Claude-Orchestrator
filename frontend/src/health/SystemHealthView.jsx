import { useCallback, useEffect, useState } from 'react'
import { Alert, Button, Spin } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import relativeTime from 'dayjs/plugin/relativeTime'
import { api } from '../api'
import { when } from '../time'
import { STATUS_WORD, detailRows, overallLine, overallOf, sortChecks } from './health'
import './health.css'

dayjs.extend(relativeTime)

const Dot = ({ status, big }) => (
  <span
    className={`co-health-dot co-health-dot-${status}${big ? ' co-health-dot-big' : ''}`}
    role="img"
    aria-label={STATUS_WORD[status] || status}
  />
)

export function HealthCard({ check }) {
  const rows = detailRows(check.detail)
  const hasMore = rows.length > 0 || !!check.fix
  return (
    <div className="co-surface co-health-card" data-testid={`health-${check.id}`} data-status={check.status}>
      <div className="co-health-head">
        <Dot status={check.status} />
        <span>{check.title}</span>
      </div>
      <p className="co-health-summary">{check.summary}</p>
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
          <HealthCard key={c.id} check={c} />
        ))}
      </div>
    </div>
  )
}
