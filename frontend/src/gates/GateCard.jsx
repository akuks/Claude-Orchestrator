import { useState } from 'react'
import { Alert, Button, Tag, message } from 'antd'
import { api } from '../api'

const OUTCOME_COLOR = { passed: 'success', failed: 'error', refused: 'error', timeout: 'error', timed_out: 'error' }

// The honest state words. The server sends `status_text`; the fallback builds
// the same fixed words from the state and the live handle.
export function gateWords(stage, state, live) {
  if (state?.status_text) return state.status_text
  const status = state?.status || 'waiting'
  if (status === 'running') {
    const n = (stage?.run || []).length
    const k = (state?.passed_cmds || []).length
    return live?.command
      ? `running \`${live.command}\` (${live.k ?? k} of ${live.n ?? n} commands passed)`
      : `not yet passed (${k} of ${n} commands passed, none running)`
  }
  return status.replace('_', ' ')
}

const ms = (v) => (v == null ? null : v >= 1000 ? `${(v / 1000).toFixed(1)}s` : `${v}ms`)

function GateRun({ runId, row }) {
  const [log, setLog] = useState(null) // null = hidden; { lines, truncated } | { error }
  const [busy, setBusy] = useState(false)
  const toggle = async () => {
    if (log) return setLog(null)
    setBusy(true)
    try {
      setLog(await api.workflowGateLog(runId, row.id))
    } catch (e) {
      setLog({ error: e?.message || 'Could not load the log' })
    } finally {
      setBusy(false)
    }
  }
  const counts = [
    row.passed != null && `${row.passed} passed`,
    row.failed != null && `${row.failed} failed`,
    row.skipped != null && `${row.skipped} skipped`,
  ].filter(Boolean)
  return (
    <li className="co-gate-run" data-testid="gate-run">
      <div>
        <code>{row.command}</code> <Tag>{row.kind}</Tag>
        <Tag color={OUTCOME_COLOR[row.outcome]}>{row.outcome}</Tag>
        {row.exit_code != null && <span className="co-num">exit {row.exit_code}</span>}
        {row.duration_ms != null && <span className="co-num"> · {ms(row.duration_ms)}</span>}
        {counts.length > 0 && <span className="co-num"> · {counts.join(', ')}</span>}
        {row.flaky && (
          <Tag color="warning" data-testid="gate-flaky">
            flaky: a flaky test is not a fixed test
          </Tag>
        )}
      </div>
      {row.failing?.length > 0 && (
        <ul className="co-gate-failing" data-testid="gate-failing">
          {row.failing.map((n) => (
            <li key={n}>{n}</li>
          ))}
        </ul>
      )}
      {row.flaky?.length > 0 && <div className="co-gate-flaky-names">Failed then passed: {row.flaky.join(', ')}</div>}
      {row.has_log && (
        <>
          <Button size="small" type="link" loading={busy} onClick={toggle} data-testid="gate-show-log">
            {log ? 'Hide log' : 'Show log'}
          </Button>
          {log?.error && <Alert type="error" message={log.error} />}
          {log && !log.error && (
            <pre className="co-gate-log" data-testid="gate-log">
              {(log.truncated ? '[earlier output cut]\n' : '') + (log.lines || []).join('\n')}
            </pre>
          )}
        </>
      )}
    </li>
  )
}

// A command gate stage: its state, what is running, why it is held or blocked,
// the guard hits awaiting "Allow and run", and the commands it has run.
export default function GateCard({ runId, index, stage, state, live, gateRuns = [], canAllow = true, onChanged }) {
  const [busy, setBusy] = useState(false)
  const st = state || {}
  const mine = live && live.stage_index === index ? live : null
  const rows = gateRuns.filter((r) => r.stage_index === index)
  const allow = async () => {
    setBusy(true)
    try {
      await api.allowWorkflowGate(runId, index)
      message.success('Allowed. The gate will run.')
      await onChanged?.()
    } catch (e) {
      message.error(e?.message || 'Could not allow this gate')
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="co-gate" data-testid="gate-card">
      <div className="co-gate-words" data-testid="gate-words">
        {gateWords(stage, st, mine)}
      </div>
      {st.hidden && <div className="co-gate-hidden">The details of this gate belong to a project you can't see.</div>}
      {mine?.progress_line && (
        <div className="co-gate-progress co-num" data-testid="gate-progress">
          {mine.progress_line}
        </div>
      )}
      {st.status === 'held' && st.reason && (
        <Alert type="warning" showIcon message={`Held: ${st.reason}`} data-testid="gate-held" />
      )}
      {st.status === 'held' && st.others?.length > 0 && (
        <div className="co-gate-others">
          Also active here: {st.others.join(', ')}
          {st.others_more > 0 ? ` and ${st.others_more} more` : ''}
        </div>
      )}
      {st.status === 'failed' && st.reason && <Alert type="error" showIcon message={st.reason} data-testid="gate-blocked" />}
      {st.status === 'needs_approval' && (
        <Alert
          type="warning"
          showIcon
          data-testid="gate-needs-approval"
          message="The guard needs a person to allow this gate"
          description={
            <ul>
              {(st.hits || []).map((h) => (
                <li key={h}>{h}</li>
              ))}
            </ul>
          }
          action={
            canAllow && (
              <Button size="small" type="primary" loading={busy} onClick={allow} data-testid="gate-allow">
                Allow and run
              </Button>
            )
          }
        />
      )}
      {st.summary && <div className="co-gate-summary">{st.summary}</div>}
      {rows.length > 0 && (
        <ul className="co-gate-runs" aria-label="Gate runs">
          {rows.map((r) => (
            <GateRun key={r.id} runId={runId} row={r} />
          ))}
        </ul>
      )}
    </div>
  )
}
