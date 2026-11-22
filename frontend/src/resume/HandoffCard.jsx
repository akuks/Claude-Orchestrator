import { useCallback, useEffect, useRef, useState } from 'react'
import { Button, Space, Typography } from 'antd'
import { CheckOutlined, DownOutlined, ReloadOutlined, RightOutlined } from '@ant-design/icons'
import { api } from '../api'
import { revealHidden } from '../guard/guard'
import { stepLabel } from './resume'
import { when } from '../time'
import './resume.css'

const formatTime = (d) => when(d).format('MMM D, HH:mm')

// Everything here comes from the agent or the repo: plain text only, hidden
// characters shown as markers (the Phase 2 revealHidden helper).
const clean = (v) => revealHidden(v).text
const list = (v) => (Array.isArray(v) ? v : [])

function Row({ label, children, testid }) {
  return (
    <div className="co-resume-row" data-testid={testid}>
      <span className="co-resume-label">{label}</span>
      <span className="co-resume-text">{children}</span>
    </div>
  )
}

const ENDED = ['completed', 'failed', 'cancelled']

// "Where it stands": the thread's handoff, above the run log. `status`: the
// step's; when it ends the card asks again (the server stores the finish
// handoff before the status flips).
export default function HandoffCard({ taskId, onHandoff, canManage = false, status }) {
  const [handoff, setHandoff] = useState(undefined) // undefined: loading, null: none yet
  const [open, setOpen] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  const apply = useCallback(
    (h) => {
      setHandoff(h)
      onHandoff?.(h)
    },
    [onHandoff],
  )
  useEffect(() => {
    let live = true
    setHandoff(undefined)
    onHandoff?.(null)
    setError(null)
    if (!taskId) return undefined // no task selected (the drawer is closed): nothing to ask for
    api
      .getHandoff(taskId)
      .then((h) => live && apply(h))
      .catch((e) => live && (setError(e.message), apply(null)))
    return () => {
      live = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [taskId])
  const ended = ENDED.includes(status)
  const last = useRef({ taskId, status })
  useEffect(() => {
    const prev = last.current
    last.current = { taskId, status }
    // only when this step is seen to end (not a task change or the first status that loads: the fetch above)
    if (!taskId || !ended || !prev.status || ENDED.includes(prev.status) || prev.taskId !== taskId) return undefined
    let live = true
    api
      .getHandoff(taskId)
      .then((h) => live && apply(h))
      .catch(() => {}) // the card keeps what it shows
    return () => {
      live = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [taskId, status])

  const run = async (fn) => {
    setBusy(true)
    setError(null)
    try {
      apply(await fn(taskId))
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  if (!taskId || handoff === undefined) return null
  const c = (handoff && handoff.content) || {}
  const step = stepLabel(c.step)
  const done = list(c.done)
  const running = list(c.running)
  const waiting = list(c.waiting)
  const files = list(c.files)
  const temp = list(c.outside_docs)
  const commits = list(c.outside_commits)
  const isDone = handoff?.status === 'done'

  return (
    <section className="co-resume-card" data-testid="handoff-card" aria-label="Where it stands">
      <div className="co-resume-head">
        <Button
          type="text"
          size="small"
          icon={open ? <DownOutlined /> : <RightOutlined />}
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
          data-testid="handoff-toggle"
        >
          <span className="co-resume-title">Where it stands</span>
        </Button>
        {handoff && (
          <span className="co-resume-meta" data-testid="handoff-meta">
            {isDone ? 'marked done · ' : ''}
            {handoff.saved_at ? `saved ${formatTime(handoff.saved_at)}` : ''}
            {handoff.reason ? ` · ${clean(handoff.reason)}` : ''}
          </span>
        )}
      </div>
      {open && (
        <div className="co-resume-body">
          {!handoff ? (
            <Typography.Text type="secondary" data-testid="handoff-empty">
              Nothing saved yet. It appears once a step has run.
            </Typography.Text>
          ) : (
            <>
              {c.task && <Row label="Task" testid="handoff-task">{clean(c.task)}</Row>}
              {step && <Row label="Step" testid="handoff-step">{step}</Row>}
              {done.length > 0 && (
                <Row label="Done" testid="handoff-done">
                  {done.map((d, i) => (
                    <div key={i}>{clean(d)}</div>
                  ))}
                </Row>
              )}
              {c.next && <Row label="Next" testid="handoff-next">{clean(c.next)}</Row>}
              {running.length > 0 && (
                <Row label="Still running" testid="handoff-running">{running.map(clean).join('; ')}</Row>
              )}
              {waiting.length > 0 && (
                <Row label="Waiting" testid="handoff-waiting">{waiting.map(clean).join('; ')}</Row>
              )}
              {c.notes && c.notes.text && (
                <Row label="Claude's notes" testid="handoff-notes">
                  {clean(c.notes.text)}
                  {c.notes.at && (
                    <div className="co-resume-meta" data-testid="handoff-notes-at">
                      written {formatTime(c.notes.at)}
                    </div>
                  )}
                </Row>
              )}
              {files.length > 0 && (
                <Row label="Files" testid="handoff-files">
                  <span className="co-resume-mono">{files.map(clean).join('\n')}</span>
                </Row>
              )}
              {temp.length > 0 && (
                <Row label="Temp files" testid="handoff-temp">
                  <span className="co-resume-warn">may vanish: </span>
                  <span className="co-resume-mono">{temp.map(clean).join('\n')}</span>
                </Row>
              )}
              {commits.length > 0 && (
                <Row label="Outside commits" testid="handoff-commits">
                  <span className="co-resume-mono">
                    {commits.map((x) => `${clean(x.sha)} ${clean(x.subject)}`).join('\n')}
                  </span>
                </Row>
              )}
            </>
          )}
          {error && (
            <div className="co-resume-error" data-testid="handoff-error">
              {error}
            </div>
          )}
          <Space className="co-resume-actions" size="small">
            {canManage && (
              <Button size="small" icon={<ReloadOutlined />} loading={busy} onClick={() => run(api.refreshHandoff)} data-testid="handoff-refresh">
                Refresh
              </Button>
            )}
            {canManage && handoff && !isDone && (
              <Button size="small" icon={<CheckOutlined />} loading={busy} onClick={() => run(api.closeHandoff)} data-testid="handoff-close">
                Mark done
              </Button>
            )}
          </Space>
        </div>
      )}
    </section>
  )
}
