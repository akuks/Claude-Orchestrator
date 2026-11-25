import { useCallback, useEffect, useState } from 'react'
import { Button, Segmented, Spin, Switch, Typography } from 'antd'
import { api } from '../api'
import { plainError } from '../plainError'
import { changeLine, groupByDay } from './activity'
import './activity.css'

const BIGGER = 50
const dayLabel = (d) => new Date(`${d}T12:00:00`).toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' })

// A compact bar chart of commits per day (plain CSS bars).
function Chart({ days }) {
  const max = Math.max(1, ...days.map((d) => d.commits))
  return (
    <div className="co-act-bar" data-testid="activity-chart" role="img" aria-label="Commits per day">
      {days.map((d) => (
        <span
          key={d.date}
          className={d.commits ? '' : 'co-act-zero'}
          style={{ height: `${d.commits ? Math.max(6, (d.commits / max) * 100) : 2}%` }}
          title={`${dayLabel(d.date)}: ${d.commits} ${d.commits === 1 ? 'commit' : 'commits'}`}
        />
      ))}
    </div>
  )
}

export default function ActivitySection({ project, active = true }) {
  const [days, setDays] = useState(30)
  const [bigger, setBigger] = useState(false)
  const [data, setData] = useState(null)
  const [err, setErr] = useState('')
  const [loading, setLoading] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    setErr('')
    try {
      setData(await api.getActivity(project.id, days, bigger ? BIGGER : 0))
    } catch (e) {
      setErr(plainError(e))
    } finally {
      setLoading(false)
    }
  }, [project.id, days, bigger])

  useEffect(() => {
    if (active) load()
  }, [active, load])

  const controls = (
    <div style={{ display: 'flex', gap: 16, alignItems: 'center', flexWrap: 'wrap' }}>
      <Segmented
        value={days}
        onChange={setDays}
        options={[{ value: 7, label: '7 days' }, { value: 30, label: '30 days' }, { value: 90, label: '90 days' }]}
        data-testid="activity-days"
      />
      <label style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
        <Switch checked={bigger} onChange={setBigger} data-testid="activity-bigger" aria-label="Only bigger changes" />
        <span>Only bigger changes</span>
      </label>
    </div>
  )

  if (err) {
    return (
      <div data-testid="activity-error">
        <Typography.Text type="danger">{err}</Typography.Text> <Button size="small" onClick={load}>Try again</Button>
      </div>
    )
  }
  if (!data) return <Spin />
  if (!data.is_repo) return <p data-testid="activity-not-repo">This project's folder isn't a git repository.</p>
  const groups = groupByDay(data.commits)
  return (
    <div data-testid="activity-section" style={{ opacity: loading ? 0.6 : 1 }}>
      {controls}
      <Chart days={data.by_day || []} />
      {groups.length === 0 && <p data-testid="activity-empty">No commits in this period.</p>}
      {groups.map((g) => (
        <div key={g.date}>
          <div className="co-act-day">{dayLabel(g.date)}</div>
          {g.commits.map((c) => (
            <div key={c.sha} className="co-act-commit" data-testid="activity-commit">
              <div>{c.title}</div>
              <div className="co-act-sub">
                {c.author} · {changeLine(c)}
              </div>
              {c.task && (
                <div className="co-act-sub">
                  <a href={`#/tasks/${c.task.id}`} data-testid="activity-task-link">Made by task {c.task.title}</a>
                </div>
              )}
              <details className="co-act-sub">
                <summary>Details</summary>
                <code>{c.short}</code>
              </details>
            </div>
          ))}
        </div>
      ))}
      {data.truncated && <p className="co-act-sub">Showing the newest 2,000 commits.</p>}
      {(data.contributors || []).length > 0 && (
        <table className="co-act-table" data-testid="activity-contributors">
          <thead>
            <tr><th>Who</th><th>Commits</th><th>Lines</th></tr>
          </thead>
          <tbody>
            {data.contributors.map((p) => (
              <tr key={p.name}>
                <td>{p.name}</td>
                <td>{p.commits}</td>
                <td>+{p.added} {'−'}{p.removed}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
