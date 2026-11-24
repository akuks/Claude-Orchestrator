import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Button, Input, Spin, Tag, Typography } from 'antd'
import { KeyOutlined } from '@ant-design/icons'
import { api } from '../api'
import { sha7 } from '../codemap/codemap'
import { plainError } from '../plainError'
import { breakable } from './breakable'
import './database.css'

export const SEARCH_ABOVE = 8
const RETRY_NOTE = /took too long|could not be read/i
// "migrations/001.sql:3" -> "migrations/001.sql, line 3"
export const where = (s) => {
  const m = /^(.*):(\d+)$/.exec(s || '')
  return m ? `${m[1]}, line ${m[2]}` : s || ''
}
const plural = (n, one, many) => (n === 1 ? one : many)

// Which columns of a table link to another table: column name -> [target table, ...].
export const linkMap = (table) => {
  const m = {}
  for (const l of table.links || []) for (const c of l.columns || []) (m[c] ||= []).push(l.to_table)
  return m
}
const linkedTables = (table) => [...new Set((table.links || []).map((l) => l.to_table))]

function TableCard({ table, root, tableRef, hit, onJump, known }) {
  const byCol = linkMap(table)
  const out = linkedTables(table)
  return (
    <div ref={tableRef} className={`co-surface co-db-card${hit ? ' co-db-hit' : ''}`} data-testid="db-table">
      <div className="co-db-card-head">
        <span className="co-db-name">{breakable(table.name)}</span>
        {table.uncertain && <Tag color="orange" bordered={false}>May differ from what is shown</Tag>}
      </div>
      {out.length > 0 && <div className="co-db-sub">Links to {out.join(', ')}</div>}
      <ul className="co-db-cols">
        {table.columns.map((c) => (
          <li key={c.name} className="co-db-col">
            <span className="co-db-keyslot">
              {c.primary_key && <KeyOutlined className="co-db-key" data-testid="db-key" title="Primary key" aria-label="Primary key" />}
            </span>
            <span className="co-db-colname">{breakable(c.name)}</span>
            <span className="co-db-right">
              <span className="co-db-type">{c.type}</span>
              {(byCol[c.name] || []).map((to) =>
                known.has(`${root}\u0000${to}`) ? (
                  <button type="button" key={to} className="co-db-chip" data-testid="db-link-chip" aria-label={`links to ${to}`} onClick={() => onJump(root, to)}>
                    → {breakable(to)}
                  </button>
                ) : (
                  <span key={to} className="co-db-chip co-db-chip-off" data-testid="db-link-chip" aria-label={`links to ${to}`}>→ {breakable(to)}</span>
                ),
              )}
            </span>
          </li>
        ))}
      </ul>
    </div>
  )
}

export default function DatabaseSection({ project, active = true }) {
  const [data, setData] = useState(null)
  const [err, setErr] = useState('')
  const [loading, setLoading] = useState(false)
  const [filter, setFilter] = useState('')
  const [hit, setHit] = useState(null)
  const refs = useRef({})
  const timer = useRef(null)

  const load = useCallback(async () => {
    setLoading(true)
    setErr('')
    try {
      setData(await api.getSchema(project.id))
    } catch (e) {
      setErr(plainError(e))
    } finally {
      setLoading(false)
    }
  }, [project.id])

  // Look again each time the tab is opened (not on every render).
  useEffect(() => {
    if (active) load()
  }, [active, load])
  useEffect(() => () => clearTimeout(timer.current), [])

  const known = useMemo(() => {
    const s = new Set()
    for (const r of data?.roots || []) for (const t of r.tables) s.add(`${r.root}\u0000${t.name}`)
    return s
  }, [data])

  const jump = (root, name) => {
    setFilter('')
    const key = `${root}\u0000${name}`
    setHit(key)
    // after the filter clears, the card exists; wait one tick so it is on screen
    setTimeout(() => refs.current[key]?.scrollIntoView?.({ behavior: 'smooth', block: 'center' }), 0)
    clearTimeout(timer.current)
    timer.current = setTimeout(() => setHit(null), 2500)
  }

  if (err) {
    return (
      <div data-testid="db-error">
        <Typography.Text type="danger">{err}</Typography.Text>{' '}
        <Button size="small" onClick={load}>Try again</Button>
      </div>
    )
  }
  if (!data) return <Spin />
  const roots = data.roots || []
  if (!data.found || roots.length === 0) {
    return (
      <div className="co-db-empty" data-testid="db-empty">
        <p className="co-db-empty-note">{data.note}</p>
        <p>Migrations written as code (for example Alembic or Django) are not read.</p>
        {RETRY_NOTE.test(data.note || '') && <Button size="small" onClick={load} loading={loading} data-testid="db-retry">Try again</Button>}
      </div>
    )
  }
  const total = roots.reduce((n, r) => n + r.tables.length, 0)
  const q = filter.trim().toLowerCase()
  const nu = data.not_understood || { count: 0, samples: [] }
  return (
    <div data-testid="db-section">
      <p className="co-db-explain">
        The tables your project's database is built from, worked out by reading its migration files. Nothing was run and no database was contacted.
      </p>
      {total > SEARCH_ABOVE && (
        <Input allowClear placeholder="Find a table" value={filter} onChange={(e) => setFilter(e.target.value)} className="co-db-search" aria-label="Find a table" data-testid="db-search" />
      )}
      {roots.map((r) => {
        const tables = r.tables.filter((t) => !q || t.name.toLowerCase().includes(q))
        return (
          <div key={r.root} className="co-db-root">
            {roots.length > 1 && <div className="co-db-roothead">Group {roots.indexOf(r) + 1}</div>}
            {tables.length === 0 && <div className="co-db-sub">No tables match.</div>}
            <div className="co-db-grid">
              {tables.map((t) => {
                const key = `${r.root}\u0000${t.name}`
                return (
                  <TableCard key={t.name} table={t} root={r.root} known={known} hit={hit === key} onJump={jump} tableRef={(el) => { if (el) refs.current[key] = el }} />
                )
              })}
            </div>
          </div>
        )
      })}
      {nu.count > 0 && (
        <div className="co-db-note" data-testid="db-not-understood">
          {nu.count} {plural(nu.count, 'statement was', 'statements were')} not understood
        </div>
      )}
      {(data.limits || []).length > 0 && (
        <div className="co-db-note" data-testid="db-limits">{data.limits.join(' ')}</div>
      )}
      <details className="co-db-details" data-testid="db-details">
        <summary>Details</summary>
        <div className="co-db-dh">Files read</div>
        <dl className="co-db-dl">
          {data.as_of && (<><dt>Read at</dt><dd>{sha7(data.as_of)}</dd></>)}
          {roots.map((r, i) => (
            <Fragment key={r.root}>
              <dt>{roots.length > 1 ? `Group ${i + 1}` : 'Folder'}: {r.root}/</dt>
              <dd>{r.files} {plural(r.files, 'file', 'files')}</dd>
            </Fragment>
          ))}
        </dl>
        <div className="co-db-dh">Where each table is defined</div>
        <dl className="co-db-dl">
          {roots.flatMap((r) => r.tables.map((t) => (
            <Fragment key={`${r.root}\u0000${t.name}`}>
              <dt>{breakable(t.name)}</dt>
              <dd>{where(t.defined_at)}</dd>
              {t.links.map((l, i) => (
                <Fragment key={i}>
                  <dt className="co-db-indent">{breakable(l.columns.join(', '))} → {breakable(l.to_table)}</dt>
                  <dd>{where(l.defined_at)}</dd>
                </Fragment>
              ))}
              {t.decisions.map((d) => (
                <Fragment key={d.id}>
                  <dt className="co-db-indent">Decision {d.id}</dt>
                  <dd className="co-db-plain">{d.title}</dd>
                </Fragment>
              ))}
            </Fragment>
          )))}
        </dl>
        {nu.samples.length > 0 && (
          <>
            <div className="co-db-dh">Not understood</div>
            <dl className="co-db-dl">
              {nu.samples.map((x, i) => (<Fragment key={i}><dt>Statement</dt><dd>{where(x)}</dd></Fragment>))}
            </dl>
          </>
        )}
      </details>
    </div>
  )
}
