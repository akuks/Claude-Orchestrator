import { useEffect, useState } from 'react'
import { message } from 'antd'
import { api } from '../api'
import QuestionCard from './QuestionCard'
import { openQuestions } from './questions'
import './questions.css'

const done = (kind, res) => {
  message.success(kind === 'answered' ? 'Answered; the thread continues' : 'Question dismissed')
  // the card is gone after the refetch, so the suggestion outcome is told here
  const s = kind === 'answered' ? res?.suggestion : null
  if (typeof s === 'string' && s.startsWith('skipped')) message.warning(`Answered, but the decision suggestion was ${s}`)
  else if (s === 'added') message.info('The decision suggestion was added for review')
}

// Home: the open questions on tasks the viewer can see (the server's list), each with its task.
export default function QuestionsSection({ questions, onChanged }) {
  const list = openQuestions(questions)
  return (
    <ul className="co-q-list" data-testid="home-questions-list">
      {list.map((q) => (
        <li key={q.id}>
          <QuestionCard
            question={q}
            showTask
            onDone={(kind, res) => {
              done(kind, res)
              onChanged?.()
            }}
          />
        </li>
      ))}
    </ul>
  )
}

// TaskDetail: the thread's open questions. Refetched when `refreshKey` changes (the task's status). An
// answer opens the follow-up step via `onAnswered(child)`.
export function ThreadQuestions({ taskId, refreshKey, onAnswered, onChanged }) {
  const [list, setList] = useState([])
  const [tick, setTick] = useState(0)
  useEffect(() => setList([]), [taskId]) // never show the previous task's questions
  useEffect(() => {
    if (!taskId) return undefined // no task selected (TaskDetail stays mounted): no request
    let live = true
    api
      .taskQuestions(taskId)
      .then((rows) => live && setList(openQuestions(rows)))
      .catch(() => live && setList([]))
    return () => {
      live = false
    }
  }, [taskId, refreshKey, tick])
  if (!list.length) return null
  return (
    <section className="co-q-thread" data-testid="thread-questions" aria-label="Questions">
      <div className="co-q-thread-title">
        {list.length === 1 ? 'The agent asked a question' : `The agent asked ${list.length} questions`}
      </div>
      <ul className="co-q-list">
        {list.map((q) => (
          <li key={q.id}>
            <QuestionCard
              question={q}
              showTask={q.task_id !== taskId}
              onDone={(kind, res) => {
                done(kind, res)
                onChanged?.()
                if (kind === 'answered' && res?.task) onAnswered?.(res.task)
                else setTick((n) => n + 1)
              }}
            />
          </li>
        ))}
      </ul>
    </section>
  )
}
