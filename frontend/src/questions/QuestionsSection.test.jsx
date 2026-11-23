import { act } from 'react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { flush, render } from '../home/testRender'

vi.mock('../api', () => ({
  api: { taskQuestions: vi.fn(), answerQuestion: vi.fn(), dismissQuestion: vi.fn() },
}))
import { api } from '../api'
import { useState } from 'react'
import QuestionsSection, { ThreadQuestions } from './QuestionsSection'

const q = (id, over = {}) => ({
  id,
  task_id: 't1',
  task_title: `task ${id}`,
  status: 'open',
  can_answer: true,
  question: `question ${id}?`,
  options: [],
  recommendation: null,
  source: 'agent',
  created_at: '2026-11-22T10:00:00Z',
  ...over,
})
let view
beforeAll(() => {
  window.matchMedia ||= () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} })
})
beforeEach(() => vi.clearAllMocks())
afterEach(async () => {
  await view?.unmount()
  view = null
})

describe('QuestionsSection (Home)', () => {
  it('lists each open question with its task link', async () => {
    view = await render(<QuestionsSection questions={[q('a'), q('b', { status: 'answered' }), q('c', { can_answer: false })]} />)
    const c = view.container
    expect(c.querySelector('[data-testid="question-a"]')).not.toBeNull()
    expect(c.querySelector('[data-testid="question-b"]')).toBeNull()
    expect(c.querySelector('[data-testid="question-c"] [data-testid="question-readonly"]')).not.toBeNull()
    expect(c.querySelector('[data-testid="question-a"] a').getAttribute('href')).toBe('#/tasks/t1')
  })
})

describe('ThreadQuestions (TaskDetail)', () => {
  it("shows the thread's open questions", async () => {
    api.taskQuestions.mockResolvedValue([q('a'), q('b', { status: 'dismissed' })])
    view = await render(<ThreadQuestions taskId="t1" />)
    await flush()
    expect(api.taskQuestions).toHaveBeenCalledWith('t1')
    const c = view.container
    expect(c.querySelector('[data-testid="thread-questions"]').textContent).toContain('question a?')
    expect(c.querySelector('[data-testid="question-b"]')).toBeNull()
  })
  it('renders nothing without open questions, or when the list fails', async () => {
    api.taskQuestions.mockResolvedValue([q('a', { status: 'answered' })])
    view = await render(<ThreadQuestions taskId="t1" />)
    await flush()
    expect(view.container.querySelector('[data-testid="thread-questions"]')).toBeNull()
    await view.unmount()
    api.taskQuestions.mockRejectedValue(new Error('x'))
    view = await render(<ThreadQuestions taskId="t2" />)
    await flush()
    expect(view.container.querySelector('[data-testid="thread-questions"]')).toBeNull()
  })
  it('asks for nothing while no task is selected (found by the phase 3b e2e: GET /tasks/null/questions)', async () => {
    view = await render(<ThreadQuestions taskId={null} />)
    await flush()
    expect(api.taskQuestions).not.toHaveBeenCalled()
    expect(view.container.querySelector('[data-testid="thread-questions"]')).toBeNull()
  })
})

describe('suggestion outcome survives the card', () => {
  it('shows the skipped warning after the parent removes the answered card', async () => {
    api.answerQuestion.mockResolvedValue({ question: q('a'), task: { id: 'c' }, suggestion: 'skipped: the suggestion queue is full' })
    function Parent() {
      const [rows, setRows] = useState([q('a', { project_id: 'p1', options: ['yes'] })])
      return <QuestionsSection questions={rows} onChanged={() => setRows([])} />
    }
    view = await render(<Parent />)
    const c = view.container
    const btn = c.querySelector('[data-testid="question-option"]')
    await act(async () => btn.dispatchEvent(new MouseEvent('click', { bubbles: true })))
    await flush()
    expect(c.querySelector('[data-testid="question-a"]')).toBeNull()
    expect(document.body.textContent).toContain('the decision suggestion was skipped: the suggestion queue is full')
  })
})
