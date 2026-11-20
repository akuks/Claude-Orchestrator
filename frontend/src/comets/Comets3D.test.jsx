import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { flush, render } from '../home/testRender'
import { ThemeModeProvider } from '../theme/ThemeModeContext'
import { resetWebGLCache } from './webgl'

// Stand-ins for the lazily loaded three.js chunks: they record the props they
// got and expose the fallback hook the real ones call when WebGL fails.
const sceneProps = vi.hoisted(() => ({ last: null }))
vi.mock('../three/CometScene', () => ({
  default: (p) => {
    sceneProps.last = p
    return <div data-testid="comet-scene-3d" />
  },
}))
vi.mock('../three/CometTimeline', () => ({
  default: (p) => {
    sceneProps.last = p
    return <div data-testid="comet-timeline-3d" />
  },
}))

import Comets3D, { use3D } from './Comets3D'
import { layoutComets } from './layout'

const items = [
  { id: 'r1', kind: 'run', title: 'Release', stages: [{ name: 'Plan', state: 'done' }, { name: 'Build', state: 'current' }] },
  { id: 't1', kind: 'thread', title: 'Thread', stages: [] },
]
const { comets } = layoutComets(items)
const stages = [
  { name: 'Plan', state: 'done', task_count: 1 },
  { name: 'Build', state: 'current', task_count: 2 },
]

const realGetContext = HTMLCanvasElement.prototype.getContext
function setWebGL(available) {
  resetWebGLCache()
  if (available) {
    window.WebGLRenderingContext = function WebGLRenderingContext() {}
    HTMLCanvasElement.prototype.getContext = (kind) => (kind === 'webgl2' || kind === 'webgl' ? { getExtension: () => null } : null)
  } else {
    delete window.WebGLRenderingContext
    HTMLCanvasElement.prototype.getContext = realGetContext
  }
}

function setReduceMotion(on) {
  localStorage.setItem('co.reduceMotion', String(on))
}

let view
async function mount(props) {
  view = await render(
    <ThemeModeProvider>
      <Comets3D comets={comets} overflow={0} onOpen={() => {}} {...props} />
    </ThemeModeProvider>,
  )
  await flush()
  await flush()
  return view.container
}

const has = (c, id) => Boolean(c.querySelector(`[data-testid="${id}"]`))

beforeEach(() => {
  sceneProps.last = null
  localStorage.clear()
  window.matchMedia ||= () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} })
})
afterEach(async () => {
  await view?.unmount()
  view = null
  setWebGL(false)
  localStorage.clear()
})

describe('use3D', () => {
  it('needs WebGL and motion', () => {
    expect(use3D({ reduceMotion: false, webgl: true })).toBe(true)
    expect(use3D({ reduceMotion: true, webgl: true })).toBe(false)
    expect(use3D({ reduceMotion: false, webgl: false })).toBe(false)
  })
})

describe('Comets3D fallback selection', () => {
  it('renders the SVG scene when reduce motion is on, even with WebGL', async () => {
    setWebGL(true)
    setReduceMotion(true)
    const c = await mount()
    expect(has(c, 'comet-svg')).toBe(true)
    expect(has(c, 'comet-scene-3d')).toBe(false)
  })

  it('renders the SVG scene when WebGL is unavailable (no WebGLRenderingContext)', async () => {
    setWebGL(false)
    setReduceMotion(false)
    const c = await mount()
    expect(has(c, 'comet-svg')).toBe(true)
    expect(has(c, 'comet-scene-3d')).toBe(false)
  })

  it('renders the 3D scene when WebGL is available and motion is allowed', async () => {
    setWebGL(true)
    setReduceMotion(false)
    const c = await mount()
    expect(has(c, 'comet-scene-3d')).toBe(true)
    expect(sceneProps.last.comets).toBe(comets)
    expect(sceneProps.last.mode).toBe('light')
  })

  it('falls back to SVG when the 3D scene reports it cannot render', async () => {
    setWebGL(true)
    setReduceMotion(false)
    const c = await mount()
    await act(async () => sceneProps.last.onFallback())
    expect(has(c, 'comet-svg')).toBe(true)
    expect(has(c, 'comet-scene-3d')).toBe(false)
  })

  it('timeline: SVG fallback lists the stages as buttons that select a stage', async () => {
    setWebGL(false)
    const onSelectStage = vi.fn()
    const c = await mount({ variant: 'timeline', stages, onSelectStage })
    expect(has(c, 'comet-timeline-svg')).toBe(true)
    const buttons = c.querySelectorAll('[data-testid="timeline-stage"]')
    expect([...buttons].map((b) => b.textContent)).toEqual([
      expect.stringContaining('Plan'),
      expect.stringContaining('Build'),
    ])
    await act(async () => buttons[1].click())
    expect(onSelectStage).toHaveBeenCalledWith(stages[1], 1)
  })

  it('timeline: 3D when WebGL is available', async () => {
    setWebGL(true)
    setReduceMotion(false)
    const c = await mount({ variant: 'timeline', stages })
    expect(has(c, 'comet-timeline-3d')).toBe(true)
    expect(sceneProps.last.stages).toBe(stages)
  })
})
