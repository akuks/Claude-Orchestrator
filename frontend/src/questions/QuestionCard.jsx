import { useState } from 'react'
import { Button, Input, Tooltip } from 'antd'
import { CheckOutlined, CloseOutlined, SendOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import relativeTime from 'dayjs/plugin/relativeTime'
import { api } from '../api'
import { parseTime } from '../time'
import { questionView } from './questions'
import './questions.css'

dayjs.extend(relativeTime)

// One question an agent asked a person. Every text is agent-derived: plain text, hidden characters revealed.
// The buttons are there only when the server says the viewer may answer (`can_answer`); the server still
// has the last word, and a 409 is shown as its message. `onDone(kind, result)`: kind 'answered' (result
// {question, task}) or 'dismissed' (result: the question).
export default function QuestionCard({ question: q, onDone, showTask = false }) {
  const [busy, setBusy] = useState(null)
  const [error, setError] = useState(null)
  const [text, setText] = useState('')
  const v = questionView(q)
  const open = q.status === 'open'
  const can = open && q.can_answer === true

  const run = async (key, fn, kind) => {
    setBusy(key)
    setError(null)
    try {
      const res = await fn()
      onDone?.(kind, res)
    } catch (e) {
      setError(e.status === 409 ? `Cannot answer now: ${e.message}` : e.message)
    } finally {
      setBusy(null)
    }
  }
  const answer = (key, body) => run(key, () => api.answerQuestion(q.id, body), 'answered')
  const free = text.trim()

  return (
    <article className="co-q-card" data-testid={`question-${q.id}`} aria-label="Question from an agent">
      <header className="co-q-head">
        <span className="co-q-source">{v.source}</span>
        {showTask && q.task_title && (
          <a className="co-q-task" href={`#/tasks/${encodeURIComponent(q.task_id)}`} title={q.task_title}>
            {q.task_title}
          </a>
        )}
        {q.created_at && (
          <Tooltip title={dayjs(parseTime(q.created_at)).format('YYYY-MM-DD HH:mm')}>
            <span className="co-q-meta">{dayjs(parseTime(q.created_at)).fromNow()}</span>
          </Tooltip>
        )}
        <span className={`co-q-chip${v.reversible.warn ? ' co-q-chip-warn' : ''}`} data-testid="question-reversible">
          {v.reversible.text}
        </span>
        {v.hidden > 0 && (
          <span className="co-q-chip co-q-chip-warn" data-testid="question-hidden">
            {v.hidden} hidden character{v.hidden === 1 ? '' : 's'} (shown as ⟦U+…⟧)
          </span>
        )}
      </header>
      <p className="co-q-text" data-testid="question-text">
        {v.question}
      </p>
      {v.blocks && (
        <div className="co-q-line" data-testid="question-blocks">
          <span className="co-q-label">Waiting on it:</span> {v.blocks}
        </div>
      )}
      {v.recommendation && !v.recInOptions && (
        <div className="co-q-line" data-testid="question-recommendation">
          <span className="co-q-label">Recommended:</span> {v.recommendation}
        </div>
      )}
      {!can && v.options.length > 0 && (
        <ul className="co-q-options-list">
          {v.options.map((o, i) => (
            <li key={i}>{o.recommended ? `${o.text} (recommended)` : o.text}</li>
          ))}
        </ul>
      )}

      {can && (
        <div className="co-q-actions">
          {v.options.length > 0 && (
            <div className="co-q-options">
              {v.options.map((o, i) => (
                <Button
                  key={i}
                  size="small"
                  type={o.recommended ? 'primary' : 'default'}
                  data-testid="question-option"
                  loading={busy === `opt:${i}`}
                  disabled={busy != null}
                  onClick={() => answer(`opt:${i}`, { answer: o.value })}
                >
                  {o.recommended ? `${o.text} (recommended)` : o.text}
                </Button>
              ))}
            </div>
          )}
          <div className="co-q-free">
            <Input.TextArea
              value={text}
              onChange={(e) => setText(e.target.value)}
              autoSize={{ minRows: 1, maxRows: 4 }}
              placeholder="Or write your answer…"
              data-testid="question-answer-input"
              disabled={busy != null}
            />
            <Button
              size="small"
              icon={<SendOutlined />}
              data-testid="question-send"
              disabled={!free || busy != null}
              loading={busy === 'free'}
              onClick={() => answer('free', { answer: free })}
            >
              Answer
            </Button>
          </div>
          <div className="co-q-buttons">
            {q.recommendation && (
              <Button
                size="small"
                icon={<CheckOutlined />}
                data-testid="question-accept"
                disabled={busy != null}
                loading={busy === 'accept'}
                onClick={() => answer('accept', { accept: true })}
              >
                Accept recommendation
              </Button>
            )}
            <Button
              size="small"
              type="text"
              icon={<CloseOutlined />}
              data-testid="question-dismiss"
              disabled={busy != null}
              loading={busy === 'dismiss'}
              onClick={() => run('dismiss', () => api.dismissQuestion(q.id), 'dismissed')}
            >
              Dismiss
            </Button>
          </div>
        </div>
      )}
      {open && !can && (
        <div className="co-q-meta" data-testid="question-readonly">
          Only someone who may continue this thread can answer.
        </div>
      )}
      {!open && (
        <div className="co-q-line" data-testid="question-state">
          {q.status === 'answered' ? (
            <>
              <span className="co-q-label">Answered:</span> {v.answer}
            </>
          ) : (
            <span className="co-q-label">Dismissed</span>
          )}
        </div>
      )}
      {error && (
        <div className="co-q-error" data-testid="question-error">
          {error}
        </div>
      )}
    </article>
  )
}
