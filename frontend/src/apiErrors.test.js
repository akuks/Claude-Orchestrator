import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from './api'

const answer = (status, body) =>
  vi.fn(async () => ({ ok: false, status, statusText: 'Unprocessable', json: async () => body }))

describe('api errors', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('a list of plain sentences comes through as the list, one per line', async () => {
    const sentences = ['Step 4 waits for step 6, which comes later.', 'Run at most 1 to 4 steps at once.']
    vi.stubGlobal('fetch', answer(422, { detail: sentences }))
    const e = await api.updateWorkflow('w1', {}).catch((x) => x)
    expect(e.details).toEqual(sentences)
    expect(e.message).toBe(sentences.join('\n'))
    expect(e.status).toBe(422)
  })
  it('field errors and plain text still work', async () => {
    vi.stubGlobal('fetch', answer(422, { detail: [{ loc: ['body', 'name'], msg: 'Field required' }] }))
    expect((await api.updateWorkflow('w1', {}).catch((x) => x)).message).toBe('name: Field required')
    vi.stubGlobal('fetch', answer(400, { detail: 'No.' }))
    const e = await api.updateWorkflow('w1', {}).catch((x) => x)
    expect(e.message).toBe('No.')
    expect(e.details).toBeUndefined()
  })
})
