import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useRef } from 'react'
import { render } from '../home/testRender'

// A stand-in engine: no WebGL in jsdom, and the hook is what's under test.
const engines = vi.hoisted(() => ({ list: [], fail: false }))
vi.mock('./scene', () => ({
  createEngine: vi.fn((canvas, opts) => {
    if (engines.fail) throw new Error('no webgl')
    const e = {
      canvas,
      opts,
      size: { w: 1, h: 1 },
      setRunning: vi.fn(),
      setSize: vi.fn(),
      setMode: vi.fn(),
      renderOnce: vi.fn(),
      dispose: vi.fn(),
    }
    engines.list.push(e)
    return e
  }),
}))

import { useEngine } from './useEngine'

function Host(props) {
  const wrapRef = useRef(null)
  useEngine({ wrapRef, mode: props.mode || 'light', ...props })
  return <div ref={wrapRef} data-testid="wrap" />
}

describe('useEngine', () => {
  let view
  beforeEach(() => {
    engines.list = []
    engines.fail = false
  })
  afterEach(async () => {
    await view?.unmount()
    view = null
  })

  it('mounts a canvas, follows the mode, and on unmount disposes the engine and removes the canvas', async () => {
    const onDispose = vi.fn()
    view = await render(<Host onDispose={onDispose} />)
    const wrap = view.container.querySelector('[data-testid="wrap"]')
    const [engine] = engines.list
    expect(engines.list).toHaveLength(1)
    expect(wrap.querySelector('canvas')).toBe(engine.canvas)

    await view.rerender(<Host onDispose={onDispose} mode="night" />)
    expect(engine.setMode).toHaveBeenLastCalledWith('night')

    await view.unmount()
    view = null
    expect(onDispose).toHaveBeenCalledWith(engine)
    expect(engine.dispose).toHaveBeenCalledTimes(1)
    expect(engine.canvas.isConnected).toBe(false)
    // listeners are gone: a later visibility change doesn't touch the dead engine
    const calls = engine.setRunning.mock.calls.length
    document.dispatchEvent(new Event('visibilitychange'))
    expect(engine.setRunning.mock.calls.length).toBe(calls)
  })

  it('falls back (and leaves no canvas) when the engine cannot start', async () => {
    engines.fail = true
    const onFallback = vi.fn()
    view = await render(<Host onFallback={onFallback} />)
    expect(onFallback).toHaveBeenCalledTimes(1)
    expect(view.container.querySelector('canvas')).toBeNull()
  })

  it('a lost WebGL context switches to the fallback without asking the browser to restore it', async () => {
    const onFallback = vi.fn()
    view = await render(<Host onFallback={onFallback} />)
    const [engine] = engines.list
    const ev = new Event('webglcontextlost', { cancelable: true })
    engine.canvas.dispatchEvent(ev)
    expect(onFallback).toHaveBeenCalledTimes(1)
    // preventDefault would signal we'll handle webglcontextrestored, which we don't
    expect(ev.defaultPrevented).toBe(false)
  })
})
