import { act } from 'react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { flush, render } from '../home/testRender'

vi.mock('../api', () => ({ api: { answerQuestion: vi.fn(), dismissQuestion: vi.fn() } }))
import { api } from '../api'
import QuestionCard from './QuestionCard'

const base = {
  id: 'q1',
  task_id: 't1',
  task_title: 'Port the table',
  status: 'open',
  can_answer: true,
  question: 'Migrate the old table too?',
  options: ['yes', 'no'],
  recommendation: 'yes',
  blocks: 'the release notes',
  reversible: false,
  source: 'agent',
  created_at: '2026-11-22T10:00:00Z',
}
const $ = (c, id) => c.querySelector(`[data-testid="${id}"]`)
const click = (el) => act(async () => el.dispatchEvent(new MouseEvent('click', { bubbles: true })))
const type = (el, value) =>
  act(async () => {
    const set = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set
    set.call(el, value)
    el.dispatchEvent(new Event('input', { bubbles: true }))
  })

let view
const mount = async (props = {}) => {
  view = await render(<QuestionCard question={base} {...props} />)
  await flush()
  return view.container
}
beforeAll(() => {
  window.matchMedia ||= () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} })
})
beforeEach(() => vi.clearAllMocks())
afterEach(async () => {
  await view?.unmount()
  view = null
})

describe('QuestionCard', () => {
  it('shows the question, options with the recommendation marked, blocks and the reversible chip', async () => {
    const c = await mount()
    expect($(c, 'question-text').textContent).toBe('Migrate the old table too?')
    const opts = [...c.querySelectorAll('[data-testid="question-option"]')].map((b) => b.textContent)
    expect(opts).toEqual(['yes (recommended)', 'no'])
    expect($(c, 'question-blocks').textContent).toContain('the release notes')
    expect($(c, 'question-reversible').textContent).toBe('not reversible')
    expect($(c, 'question-accept')).not.toBeNull()
    expect($(c, 'question-dismiss')).not.toBeNull()
    expect($(c, 'question-answer-input')).not.toBeNull()
  })

  it('reveals hidden characters and says so', async () => {
    const c = await mount({ question: { ...base, question: 'ok‮?' } })
    expect($(c, 'question-text').textContent).toContain('⟦U+202E⟧')
    expect($(c, 'question-hidden').textContent).toContain('hidden character')
  })

  it('has no buttons when the viewer may not answer', async () => {
    const c = await mount({ question: { ...base, can_answer: false } })
    expect(c.querySelector('[data-testid="question-option"]')).toBeNull()
    expect($(c, 'question-accept')).toBeNull()
    expect($(c, 'question-dismiss')).toBeNull()
    expect($(c, 'question-answer-input')).toBeNull()
    expect($(c, 'question-readonly')).not.toBeNull()
  })

  it('an answered question shows the answer, no buttons', async () => {
    const c = await mount({ question: { ...base, status: 'answered', answer: 'no', can_answer: false } })
    expect($(c, 'question-state').textContent).toContain('Answered')
    expect($(c, 'question-state').textContent).toContain('no')
    expect($(c, 'question-accept')).toBeNull()
  })

  it('no Accept without a recommendation', async () => {
    const c = await mount({ question: { ...base, recommendation: null } })
    expect($(c, 'question-accept')).toBeNull()
  })

  it('an option answers with its text; Accept sends accept; Dismiss dismisses', async () => {
    const onDone = vi.fn()
    api.answerQuestion.mockResolvedValue({ question: { ...base, status: 'answered' }, task: { id: 'child' } })
    api.dismissQuestion.mockResolvedValue({ ...base, status: 'dismissed' })
    const c = await mount({ onDone })
    await click(c.querySelectorAll('[data-testid="question-option"]')[1])
    await flush()
    expect(api.answerQuestion).toHaveBeenLastCalledWith('q1', { answer: 'no' })
    expect(onDone).toHaveBeenLastCalledWith('answered', expect.objectContaining({ task: { id: 'child' } }))
    await click($(c, 'question-accept'))
    await flush()
    expect(api.answerQuestion).toHaveBeenLastCalledWith('q1', { accept: true })
    await click($(c, 'question-dismiss'))
    await flush()
    expect(api.dismissQuestion).toHaveBeenCalledWith('q1')
    expect(onDone).toHaveBeenLastCalledWith('dismissed', expect.objectContaining({ status: 'dismissed' }))
  })

  it('a free answer is sent trimmed; empty sends nothing', async () => {
    api.answerQuestion.mockResolvedValue({ question: base, task: { id: 'c' } })
    const c = await mount()
    expect($(c, 'question-send').disabled).toBe(true)
    await type($(c, 'question-answer-input'), '  only the new one  ')
    await click($(c, 'question-send'))
    await flush()
    expect(api.answerQuestion).toHaveBeenCalledWith('q1', { answer: 'only the new one' })
  })

  it('a 409 is shown as its message', async () => {
    const e = Object.assign(new Error('A step of this thread is still active'), { status: 409 })
    api.answerQuestion.mockRejectedValue(e)
    const c = await mount()
    await click($(c, 'question-accept'))
    await flush()
    expect($(c, 'question-error').textContent).toBe('Cannot answer now: A step of this thread is still active')
  })

  it('suggest checkbox only with a project and can_answer; checked adds suggest_decision to every answer', async () => {
    let c = await mount()
    expect($(c, 'question-suggest')).toBeNull()
    await view.unmount()
    c = await mount({ question: { ...base, project_id: 'p1', can_answer: false } })
    expect($(c, 'question-suggest')).toBeNull()
    await view.unmount()
    api.answerQuestion.mockResolvedValue({ question: base, task: { id: 'c' }, suggestion: 'added' })
    c = await mount({ question: { ...base, project_id: 'p1' } })
    expect($(c, 'question-suggest')).not.toBeNull()
    await click($(c, 'question-suggest').querySelector('input') || $(c, 'question-suggest'))
    await click(c.querySelectorAll('[data-testid="question-option"]')[1])
    await flush()
    expect(api.answerQuestion).toHaveBeenLastCalledWith('q1', { answer: 'no', suggest_decision: true })
    await click($(c, 'question-accept'))
    await flush()
    expect(api.answerQuestion).toHaveBeenLastCalledWith('q1', { accept: true, suggest_decision: true })
    await type($(c, 'question-answer-input'), 'x')
    await click($(c, 'question-send'))
    await flush()
    expect(api.answerQuestion).toHaveBeenLastCalledWith('q1', { answer: 'x', suggest_decision: true })
  })

  it('unchecked sends no suggest_decision; a skipped result shows a warning line', async () => {
    api.answerQuestion.mockResolvedValue({ question: base, task: { id: 'c' }, suggestion: 'skipped: the suggestion queue is full' })
    const c = await mount({ question: { ...base, project_id: 'p1' } })
    await click(c.querySelectorAll('[data-testid="question-option"]')[1])
    await flush()
    expect(api.answerQuestion).toHaveBeenLastCalledWith('q1', { answer: 'no' })
    expect($(c, 'question-suggestion-skipped').textContent).toContain('skipped: the suggestion queue is full')
  })
})
