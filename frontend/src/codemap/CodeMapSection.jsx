import { useCallback, useEffect, useState } from 'react'
import { Button, Space, Spin, Switch, Typography } from 'antd'
import { api } from '../api'
import { EXPLAIN, SWITCH_LABEL, sha7, statusLine, statusTone } from './codemap'
import './codemap.css'

const NOT_ALLOWED = 'You do not have permission to do that here.'
const plain = (e) => (/admin can turn the code map on/i.test(e?.message || '') ? e.message : e?.status === 403 || /403|forbidden/i.test(e?.message || '') ? NOT_ALLOWED : String(e?.message || 'Something went wrong'))

export default function CodeMapSection({ project, canEdit }) {
  const [st, setSt] = useState(null)
  const [lines, setLines] = useState([])
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    try {
      const s = await api.getCodeMap(project.id)
      setSt(s)
      if (s.enabled) {
        const l = await api.getCodeMapLog(project.id).catch(() => ({ lines: [] }))
        setLines(l?.lines || [])
      }
    } catch (e) {
      setErr(plain(e))
    }
  }, [project.id])

  useEffect(() => {
    load()
  }, [load])

  // While a build runs, look again every few seconds.
  useEffect(() => {
    if (st?.status !== 'running') return undefined
    const t = setInterval(load, 4000)
    return () => clearInterval(t)
  }, [st?.status, load])

  const toggle = async (on) => {
    setErr('')
    setBusy(true)
    try {
      await api.updateProject(project.id, { code_map: on })
      await load()
    } catch (e) {
      setErr(plain(e))
    } finally {
      setBusy(false)
    }
  }
  const refresh = async () => {
    setErr('')
    setBusy(true)
    try {
      setSt(await api.refreshCodeMap(project.id))
    } catch (e) {
      setErr(plain(e))
    } finally {
      setBusy(false)
    }
  }

  if (!st) return err ? <Typography.Text type="danger" data-testid="codemap-error">{err}</Typography.Text> : <Spin />
  const line = statusLine(st)
  const showSpin = st.enabled && st.server_registered && st.status === 'running'
  return (
    <div data-testid="codemap-section">
      <Space>
        <Switch checked={!!st.enabled} disabled={!canEdit || busy} onChange={toggle} data-testid="codemap-switch" aria-label={SWITCH_LABEL} />
        <span>{SWITCH_LABEL}</span>
      </Space>
      <p className="co-codemap-explain">{EXPLAIN}</p>
      {st.enabled && (
        <>
          <div className="co-codemap-status" data-testid="codemap-status">
            {showSpin ? <Spin size="small" /> : <span className={`co-codemap-dot co-codemap-dot-${statusTone(st)}`} />}
            <span data-testid="codemap-line">{line}</span>
            {!st.server_registered && <a href="#/mcp" data-testid="codemap-tools-link">Connected tools</a>}
          </div>
          {canEdit && st.server_registered && (
            <Button size="small" onClick={refresh} disabled={busy || st.status === 'running'} data-testid="codemap-refresh">Refresh now</Button>
          )}
          <details className="co-codemap-details" data-testid="codemap-details">
            <summary>Details</summary>
            <div>Built at: {sha7(st.sha) || '—'}</div>
            <div>Last built: {st.indexed_at || '—'}</div>
            {st.next_try_at && <div>Next try: {st.next_try_at}</div>}
            {lines.length > 0 && <pre>{lines.join('\n')}</pre>}
          </details>
        </>
      )}
      {err && <div style={{ marginTop: 8 }}><Typography.Text type="danger" data-testid="codemap-error">{err}</Typography.Text></div>}
    </div>
  )
}
