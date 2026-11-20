import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { TimelineStageButton } from '../comets/TimelineSvg'
import { timelineComet } from '../comets/timeline'
import { buildComet, cometKey } from './scene'
import { placeLabel } from './labels'
import { useEngine } from './useEngine'
import './three.css'

// Run-page timeline: one comet flying left to right across the run's stages,
// in the Home scene's visual language. Each stage's name, state and task
// count is an HTML button under its star (keyboard reachable); clicking the
// star or its button selects the stage.

const PAD = { l: 56, r: 56, t: 26, b: 62 }
const HEIGHT = 176
const HIT_PX = 22

export default function CometTimeline({ stages, onSelectStage, selectedIndex, mode = 'light', onFallback }) {
  const wrapRef = useRef(null)
  const builtRef = useRef(null) // { key, built }
  const labelRefs = useRef([])
  const rectRef = useRef(null)
  const engineHolder = useRef(null)
  const [hoverStar, setHoverStar] = useState(-1)
  const comet = useMemo(() => ({ id: 'timeline', ...timelineComet(stages) }), [stages])
  const cometRef = useRef(comet)
  cometRef.current = comet

  const sync = useCallback((engine, c, force = false) => {
    if (!c.stars.length) {
      builtRef.current?.built.dispose()
      builtRef.current = null
      return
    }
    const key = cometKey(c)
    if (!force && builtRef.current?.key === key) return
    builtRef.current?.built.dispose()
    builtRef.current = { key, built: buildComet(engine, c, PAD) }
    for (const el of labelRefs.current) if (el) el._pos = null
    engine.markDirty()
    engine.renderOnce()
  }, [])

  const pt = useRef({})
  const onFrame = useCallback(() => {
    const engine = engineHolder.current?.current
    const b = builtRef.current?.built
    if (!engine || !b) return
    b.stars.forEach((s, j) => {
      const el = labelRefs.current[j]
      if (el) placeLabel(el, engine.project(s, pt.current), 'under')
    })
  }, [])

  const { engineRef, canvasRef } = useEngine({
    wrapRef,
    mode,
    onFallback,
    onFrame,
    dust: 150,
    parallax: 0.6,
    onResize: (engine) => {
      rectRef.current = null
      sync(engine, cometRef.current, true)
    },
    onDispose: () => {
      builtRef.current?.built.dispose()
      builtRef.current = null
    },
  })
  engineHolder.current = engineRef

  useEffect(() => {
    const engine = engineRef.current
    if (engine && engine.size.w > 1) sync(engine, comet)
  }, [comet, sync, engineRef])

  const select = onSelectStage ? (i) => onSelectStage(stages[i], i) : undefined

  const local = (e) => {
    const r = (rectRef.current ||= wrapRef.current.getBoundingClientRect())
    return { x: e.clientX - r.left, y: e.clientY - r.top, w: r.width, h: r.height }
  }
  const starAt = (x, y) => {
    const engine = engineRef.current
    const b = builtRef.current?.built
    if (!engine || !b) return -1
    let best = -1
    let bestD = HIT_PX
    b.stars.forEach((s, j) => {
      const p = engine.project(s, {})
      const d = Math.hypot(p.x - x, p.y - y)
      if (d < bestD) {
        bestD = d
        best = j
      }
    })
    return best
  }
  const onPointerMove = (e) => {
    const p = local(e)
    engineRef.current?.setPointer((p.x / p.w) * 2 - 1, (p.y / p.h) * 2 - 1)
    if (e.target === canvasRef.current) setHoverStar(select ? starAt(p.x, p.y) : -1)
  }
  const onPointerLeave = () => {
    rectRef.current = null
    engineRef.current?.setPointer(0, 0)
    setHoverStar(-1)
  }
  const onCanvasClick = (e) => {
    if (!select) return
    const p = local(e)
    const j = starAt(p.x, p.y)
    if (j >= 0) select(j)
  }

  return (
    <div className="co-c3d co-c3d-timeline" data-testid="comet-timeline-3d">
      <div
        ref={wrapRef}
        className={`co-c3d-stage${hoverStar >= 0 ? ' is-hover' : ''}`}
        style={{ height: HEIGHT }}
        onPointerMove={onPointerMove}
        onPointerLeave={onPointerLeave}
        onClick={(e) => e.target === canvasRef.current && onCanvasClick(e)}
      >
        <div className="co-c3d-labels" role="group" aria-label="Run stages">
          {comet.stars.map((s, j) => (
            <TimelineStageButton
              key={`${s.name}-${j}`}
              star={s}
              index={j}
              selected={selectedIndex === j}
              hovered={hoverStar === j}
              onSelect={select}
              buttonRef={(el) => {
                labelRefs.current[j] = el
              }}
              // as wide as the gap to the neighbouring stars allows; until the
              // first frame places it, it sits at its star's flat layout spot
              style={{
                maxWidth: `calc((100% - ${PAD.l + PAD.r}px) / ${Math.max(1, comet.stars.length - 1)} - 8px)`,
                '--co-tl-x': `calc(${PAD.l}px + ${s.x} * (100% - ${PAD.l + PAD.r}px))`,
                '--co-tl-y': `${Math.round(PAD.t + s.y * (HEIGHT - PAD.t - PAD.b))}px`,
              }}
            />
          ))}
        </div>
      </div>
    </div>
  )
}
