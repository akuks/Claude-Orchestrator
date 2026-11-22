import { useCallback, useEffect, useRef, useState } from 'react'
import { Button, Input, Spin, message } from 'antd'
import { api } from '../api'

export const ASK_POLL_MS = 2000
export const ASK_HINT = "Answered from the run's record, not the code. Only you see this."

export function askCost(c) {
  if (typeof c !== 'number' || !(c > 0)) return null
  return c < 0.005 ? '<$0.01' : `$${c.toFixed(2)}`
}

// "Ask about this run": questions answered from the run's record. Polls every 2 s only while one is pending.
export default function AskCard({ taskId }) {
  const [asks, setAsks] = useState([])
  const [text, setText] = useState('')
  const [sending, setSending] = useState(false)
  const cur = useRef(taskId) // the task the card shows now; a late response for another task is dropped
  cur.current = taskId

  const load = useCallback(async () => {
    try {
      const rows = await api.taskAsks(taskId)
      if (cur.current === taskId) setAsks(Array.isArray(rows) ? rows : [])
    } catch (_) {}
  }, [taskId])

  useEffect(() => {
    setAsks([])
    setText('')
    setSending(false)
    if (taskId) load()
  }, [taskId, load])

  const pending = asks.some((a) => a.status === 'pending')
  useEffect(() => {
    if (!pending) return undefined
    const id = setInterval(load, ASK_POLL_MS)
    return () => clearInterval(id)
  }, [pending, load])

  const submit = async (question) => {
    const q = question.trim()
    if (!q) return false
    setSending(true)
    try {
      const a = await api.askTask(taskId, q)
      if (cur.current === taskId) setAsks((prev) => [a, ...prev.filter((x) => x.id !== a.id)])
      return true
    } catch (e) {
      message.error(e.message)
      if (e.status === 409) load() // a pending ask from another tab: show it
      return false
    } finally {
      if (cur.current === taskId) setSending(false)
    }
  }
  const ask = async () => {
    if ((await submit(text)) && cur.current === taskId) setText('')
  }
  const blocked = pending || sending

  return (
    <section className="co-ask" data-testid="ask-card" aria-label="Ask about this run" style={{ margin: '12px 0' }}>
      <div style={{ fontWeight: 600, marginBottom: 4 }}>Ask about this run</div>
      <div style={{ display: 'flex', gap: 8 }}>
        <Input
          value={text}
          maxLength={2000}
          placeholder="Ask a question"
          onChange={(e) => setText(e.target.value)}
          onPressEnter={() => !blocked && text.trim() && ask()}
          data-testid="ask-input"
        />
        <Button type="primary" data-testid="ask-submit" disabled={!text.trim() || blocked} onClick={ask}>
          Ask
        </Button>
      </div>
      <div data-testid="ask-hint" style={{ fontSize: 12, opacity: 0.7, margin: '4px 0' }}>
        {ASK_HINT}
      </div>
      <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'grid', gap: 8 }}>
        {asks.map((a) => {
          const cost = askCost(a.cost_usd)
          return (
            <li key={a.id} data-testid={`ask-${a.id}`}>
              <div style={{ fontWeight: 500 }}>{a.question}</div>
              {a.status === 'pending' && (
                <div data-testid="ask-pending">
                  <Spin size="small" /> Answering…
                </div>
              )}
              {a.status === 'answered' && (
                <>
                  <div data-testid="ask-answer" style={{ whiteSpace: 'pre-wrap' }}>
                    {a.answer}
                  </div>
                  {cost && (
                    <div data-testid="ask-cost" style={{ fontSize: 12, opacity: 0.7 }}>
                      {cost}
                    </div>
                  )}
                </>
              )}
              {a.status === 'failed' && (
                <div data-testid="ask-failed">
                  <span data-testid="ask-error">{a.error || 'The question could not be answered.'}</span>{' '}
                  <Button size="small" data-testid="ask-retry" disabled={blocked} onClick={() => submit(a.question)}>
                    Try again
                  </Button>
                </div>
              )}
            </li>
          )
        })}
      </ul>
    </section>
  )
}
