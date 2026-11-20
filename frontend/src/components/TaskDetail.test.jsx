import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { flush, render } from '../home/testRender'

vi.mock('../api', () => ({
  api: {
    getTask: vi.fn(async (id) => ({ id, title: `Task ${id}`, status: 'running', prompt: 'p' })),
    getThread: vi.fn(async () => []),
    getArtifacts: vi.fn(async () => []),
    streamUrl: (id, seq) => `ws://test/tasks/${id}/stream?after=${seq}`,
    reportUrl: () => '#',
    artifactUrl: () => '#',
  },
  notifyUnauthorized: vi.fn(),
  STATUS_COLORS: {},
  PRIORITY_COLORS: {},
  shortModel: (m) => m,
}))

import TaskDetail from './TaskDetail'

// A fake socket that records what the component opens and closes.
class FakeSocket {
  static all = []
  constructor(url) {
    this.url = url
    this.closed = false
    FakeSocket.all.push(this)
  }
  close() {
    this.closed = true
  }
}

describe('TaskDetail inline: live stream lifecycle', () => {
  let view
  const RealWS = globalThis.WebSocket
  beforeAll(() => {
    window.matchMedia ||= () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} })
  })
  afterEach(async () => {
    await view?.unmount()
    view = null
    globalThis.WebSocket = RealWS
    FakeSocket.all = []
  })

  it('closes the old socket on task switch and the current one on unmount', async () => {
    globalThis.WebSocket = FakeSocket
    view = await render(<TaskDetail inline taskId="a" />)
    await flush()
    expect(FakeSocket.all.map((s) => s.url)).toEqual(['ws://test/tasks/a/stream?after=0'])
    expect(view.container.querySelector('[data-testid="task-detail-inline"]').textContent).toContain('Task a')

    await view.rerender(<TaskDetail inline taskId="b" />)
    await flush()
    expect(FakeSocket.all).toHaveLength(2)
    expect(FakeSocket.all[0].closed).toBe(true)
    expect(FakeSocket.all[1].url).toContain('/tasks/b/')
    expect(FakeSocket.all[1].closed).toBe(false)
    expect(view.container.textContent).toContain('Task b')
    expect(view.container.textContent).not.toContain('Task a')

    await view.unmount()
    view = null
    expect(FakeSocket.all[1].closed).toBe(true)
    expect(FakeSocket.all).toHaveLength(2)
  })
})
