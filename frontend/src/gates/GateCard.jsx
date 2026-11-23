import { useState } from 'react'
import { Alert, Button, Tag, message } from 'antd'
import { api } from '../api'
import './workflows.css'

const OUTCOME_COLOR = { passed: 'success', failed: 'error', refused: 'error', timeout: 'error', timed_out: 'error' }
const OUTCOME_WORDS = { passed: 'Passed', failed: 'Failed', refused: 'Not allowed', timeout: 'Took too long', timed_out: 'Took too long' }
const RUN_KIND_WORDS = { full: 'Full run', retry: 'Re-run of failures' }
const TIMED_OUT = new Set(['timeout', 'timed_out'])

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

const FIX_STATUS = {
  queued: 'Waiting to start',
  running: 'Working on it',
  awaiting_approval: 'Waiting for approval',
  completed: 'Done',
  failed: 'Did not finish',
  cancelled: 'Cancelled',
}

// The fix tasks of a check step, oldest first, with their try number (from the
// server's title "…: fix round k", else their position).
export function fixAttempts(tasks = [], index) {
  return tasks
    .filter((t) => t.stage_index === index)
    .map((t, i) => {
      const m = String(t.title || '').match(/fix round (\d+)\s*$/)
      return { task: t, k: m ? Number(m[1]) : i + 1, status: FIX_STATUS[t.status] || String(t.status || '').replace(/_/g, ' ') }
    })
}

const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`

// The plain sentence a check step leads with, and its tone. `rows` are this
// step's command runs, oldest first.
export function gateLead(state, rows = [], stage = null) {
  const status = state?.status || 'waiting'
  if (status === 'fixing') {
    const n = Number(stage?.fix?.rounds) || 1
    const k = Number(state?.fix_round) || 1
    return { tone: 'running', text: `The check failed. An agent is fixing it (try ${k} of ${n}).` }
  }
  if (status === 'budget') return { tone: 'waiting', text: 'Paused — this run reached its spending limit' }
  const last = rows[rows.length - 1]
  if (status === 'passed') {
    const full = [...rows].reverse().find((r) => r.kind !== 'retry') || last
    if (full?.passed != null) return { tone: 'passed', text: `Passed — ${plural(full.passed, 'test', 'tests')} passed` }
    return { tone: 'passed', text: 'Passed — the commands succeeded' }
  }
  if (status === 'failed') {
    if (last?.failed) {
      const total = (last.passed || 0) + last.failed
      return { tone: 'failed', text: `Failed — ${last.failed} of ${plural(total, 'test', 'tests')} failed` }
    }
    if (last && TIMED_OUT.has(last.outcome)) return { tone: 'failed', text: 'Failed — the check did not finish in time' }
    return { tone: 'failed', text: 'Failed — the check did not pass' }
  }
  if (status === 'running') return { tone: 'running', text: 'Running the check…' }
  if (status === 'held') return { tone: 'waiting', text: 'On hold — waiting until it is ready to run' }
  if (status === 'needs_approval') return { tone: 'waiting', text: 'Needs your OK before it runs' }
  if (status === 'waiting') return { tone: 'waiting', text: 'Not started yet' }
  const s = status.replace(/_/g, ' ')
  return { tone: 'waiting', text: s[0].toUpperCase() + s.slice(1) }
}


const ms = (v) => (v == null ? null : v >= 1000 ? `${(v / 1000).toFixed(1)}s` : `${v}ms`)

// Why a failed check failed, in plain words. The server's reason starts with
// "<command>, …" when a command ran; anything else (a refusal by the guard, a
// missing project) is shown as the server wrote it.
export function failureMessage(reason, last) {
  if (!reason) return null
  const ran = last?.command && String(reason).startsWith(`${last.command}, `)
  if (ran && TIMED_OUT.has(last.outcome))
    return (
      <>
        The command <code>{last.command}</code> took too long and was stopped
      </>
    )
  if (ran && last.exit_code != null)
    return (
      <>
        The command <code>{last.command}</code> failed (exit code {last.exit_code})
      </>
    )
  return reason
}

function LogToggle({ runId, row, testid }) {
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
  return (
    <div>
      <Button size="small" type="link" style={{ paddingInline: 0 }} loading={busy} onClick={toggle} data-testid={testid}>
        {log ? 'Hide the log' : 'Show the last lines of the log'}
      </Button>
      {log?.error && <Alert type="error" message={log.error} />}
      {log && !log.error && (
        <pre className="co-gate-log" data-testid={testid === 'gate-show-log' ? 'gate-log' : 'gate-log-row'}>
          {(log.truncated ? '[earlier output cut]\n' : '') + (log.lines || []).join('\n')}
        </pre>
      )}
    </div>
  )
}

// One command run, inside "Details": the command, full run or re-run, the
// outcome, exit code, time taken and counts.
function GateRun({ runId, row }) {
  const counts = [
    row.passed != null && `${row.passed} passed`,
    row.failed != null && `${row.failed} failed`,
    row.skipped != null && `${row.skipped} skipped`,
  ].filter(Boolean)
  return (
    <li className="co-gate-run" data-testid="gate-run">
      <div>
        <code>{row.command}</code> {RUN_KIND_WORDS[row.kind] && <Tag>{RUN_KIND_WORDS[row.kind]}</Tag>}
        <Tag color={OUTCOME_COLOR[row.outcome]}>{OUTCOME_WORDS[row.outcome] || row.outcome}</Tag>
        {row.exit_code != null && <span className="co-num">exit code {row.exit_code}</span>}
        {row.duration_ms != null && <span className="co-num"> · {ms(row.duration_ms)}</span>}
        {counts.length > 0 && <span className="co-num"> · {counts.join(', ')}</span>}
      </div>
      {row.has_log && <LogToggle runId={runId} row={row} testid="gate-show-log-row" />}
    </li>
  )
}

// A "Run a check" step: a plain sentence with the test result first, the
// failing tests, anything that needs a person, then the details collapsed.
export default function GateCard({ runId, index, stage, state, live, gateRuns = [], canAllow = true, onChanged, tasks = [], onOpenTask }) {
  const [busy, setBusy] = useState(false)
  const st = state || {}
  const mine = live && live.stage_index === index ? live : null
  const rows = gateRuns.filter((r) => r.stage_index === index)
  const last = rows[rows.length - 1]
  const lead = gateLead(st, rows, stage)
  const attempts = fixAttempts(tasks, index)
  const fixTask = st.status === 'fixing' && st.fix_task ? tasks.find((t) => t.id === st.fix_task) : null
  const words = gateWords(stage, st, mine)
  const showWords = words && words !== (st.status || 'waiting').replace('_', ' ')
  const failing = st.status === 'failed' && last?.failing?.length ? last.failing : []
  const flakyRow = [...rows].reverse().find((r) => r.flaky != null)
  const logRow = [...rows].reverse().find((r) => r.has_log)
  const allow = async () => {
    setBusy(true)
    try {
      await api.allowWorkflowGate(runId, index)
      message.success('Allowed. The check will run.')
      await onChanged?.()
    } catch (e) {
      message.error(e?.message || 'Could not allow this check')
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="co-gate" data-testid="gate-card">
      <div className={`co-gate-lead co-gate-lead-${lead.tone}`} data-testid="gate-lead">
        {lead.text}
      </div>
      {st.status === 'fixing' && st.reason && (
        <div className="co-gate-words" data-testid="gate-fix-reason">
          What failed: {failureMessage(st.reason, last)}
        </div>
      )}
      {fixTask && onOpenTask && (
        <Button size="small" type="link" style={{ paddingInline: 0 }} onClick={() => onOpenTask(fixTask.id)} data-testid="gate-fix-task">
          See what the agent is doing
        </Button>
      )}
      {showWords && (
        <div className="co-gate-words" data-testid="gate-words">
          {words}
        </div>
      )}
      {mine?.progress_line && (
        <div className="co-gate-progress co-num" data-testid="gate-progress">
          {mine.progress_line}
        </div>
      )}
      {failing.length > 0 && (
        <div>
          <div className="co-gate-failing-title">{failing.length === 1 ? 'The failing test' : 'The failing tests'}</div>
          <ul className="co-gate-failing" data-testid="gate-failing">
            {failing.map((n) => (
              <li key={n}>{n}</li>
            ))}
          </ul>
        </div>
      )}
      {flakyRow?.flaky && (
        <div>
          <Tag color="warning" data-testid="gate-flaky">
            flaky: a flaky test is not a fixed test
          </Tag>
          {flakyRow.flaky.length > 0 && <div className="co-gate-flaky-names">Failed then passed: {flakyRow.flaky.join(', ')}</div>}
          {flakyRow.flaky.length === 0 && (
            <div className="co-gate-flaky-names" data-testid="gate-flaky-unchanged">
              The command failed then passed unchanged
            </div>
          )}
        </div>
      )}
      {st.hidden && <div className="co-gate-hidden">The details of this check belong to a project you can't see.</div>}
      {st.status === 'held' && st.reason && (
        <Alert type="warning" showIcon message={`Not ready yet: ${st.reason}`} data-testid="gate-held" />
      )}
      {st.status === 'held' && st.others?.length > 0 && (
        <div className="co-gate-others">
          Also active here: {st.others.join(', ')}
          {st.others_more > 0 ? ` and ${st.others_more} more` : ''}
        </div>
      )}
      {st.status === 'failed' && st.reason && <Alert type="error" showIcon message={failureMessage(st.reason, last)} data-testid="gate-blocked" />}
      {st.status === 'needs_approval' && (
        <Alert
          type="warning"
          showIcon
          data-testid="gate-needs-approval"
          message="These commands need a person's OK before they run"
          description={
            <ul style={{ margin: 0, paddingLeft: 18 }}>
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
      {attempts.length > 0 && (
        <div className="co-gate-fixes" data-testid="gate-fix-attempts">
          <div className="co-gate-failing-title">Fix attempts</div>
          <ul className="co-gate-runs">
            {attempts.map((a) => (
              <li key={a.task.id} className="co-gate-run" data-testid="gate-fix-attempt">
                <span>Try {a.k}</span> <Tag>{a.status}</Tag>
                {onOpenTask && (
                  <Button size="small" type="link" onClick={() => onOpenTask(a.task.id)} data-testid="gate-fix-attempt-open">
                    Open
                  </Button>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
      {logRow && <LogToggle key={logRow.id} runId={runId} row={logRow} testid="gate-show-log" />}
      {rows.length > 0 && (
        <details className="co-gate-details" data-testid="gate-details">
          <summary>Details</summary>
          <ul className="co-gate-runs" aria-label="Each run of the commands">
            {rows.map((r) => (
              <GateRun key={r.id} runId={runId} row={r} />
            ))}
          </ul>
        </details>
      )}
    </div>
  )
}
