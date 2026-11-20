import { useCallback, useEffect, useRef, useState } from 'react'
import { CometList, stateLabel } from '../comets/CometSvg'
import { laneY } from '../comets/layout'
import { buildComet, cometKey, hitTest } from './scene'
import { placeLabel, stackLabels } from './labels'
import { useEngine } from './useEngine'
import './three.css'

// Home "Live workflows" scene: each live run / coordinator thread is a comet
// flying through its stage stars. The canvas is decoration plus a pointer
// target; titles and stages are HTML labels that follow the projected heads,
// and the list under the canvas is the keyboard path to every comet.

// A label column on the left: each comet's label sits in its own lane and
// the comet's tail flies out of it, so labels never cover another comet.
// With many comets the lanes keep to a narrow rise, so the layout's top and
// bottom margins are trimmed to use the height.
const padFor = (w, h, n) => {
  const v = n > 3 ? Math.round(30 - 0.08 * h) : 30
  return { l: Math.round(Math.min(270, Math.max(160, w * 0.32))), r: 34, t: v, b: v }
}
const canvasHeight = (n) => Math.max(260, Math.min(n, 12) * 48 + 72)
const LEAD_X = -0.07 // where the tail starts (layout x), just right of the label

export default function CometScene({ comets = [], overflow = 0, onOpen, onMore, mode = 'light', onFallback }) {
  const wrapRef = useRef(null)
  const builtRef = useRef(new Map()) // id → { key, built }
  const labelRefs = useRef(new Map()) // id | id:stage → element
  const rectRef = useRef(null)
  const hoverRef = useRef(null)
  const engineHolder = useRef(null)
  const cometsRef = useRef(comets)
  cometsRef.current = comets
  const showStages = comets.length <= 3
  const showStagesRef = useRef(showStages)
  showStagesRef.current = showStages
  const [hover, setHover] = useState(null)

  const sync = useCallback((engine, list, force = false) => {
    const prev = builtRef.current
    const next = new Map()
    list.forEach((c, i) => {
      const key = `${cometKey(c)}|${i}/${list.length}`
      const old = prev.get(c.id)
      if (old && old.key === key && !force) {
        next.set(c.id, old)
        return
      }
      old?.built.dispose()
      prev.delete(c.id)
      // the tail starts in the label column, on the comet's own lane
      // a little toward the first star, so the tail swoops in gently
      const leadIn = { x: LEAD_X, y: laneY(i, list.length) * 0.65 + c.stars[0].y * 0.35 }
      const pad = padFor(engine.size.w, engine.size.h, list.length)
      next.set(c.id, { key, built: buildComet(engine, c, pad, { leadIn }) })
    })
    for (const [id, v] of prev) if (!next.has(id)) v.built.dispose()
    builtRef.current = next
    for (const el of labelRefs.current.values()) {
      el._pos = null // re-place next frame
      el._h = el.offsetHeight // one layout read per data change, never per frame
    }
    engine.markDirty()
    engine.renderOnce()
  }, [])

  // Per frame: comet labels form a column just left of the projected tails,
  // each as close to its own tail's height as the others allow; stage names
  // (few comets only) sit under their stars. Transform writes only.
  const onFrame = useCallback(() => {
    const engine = engineHolder.current?.current
    if (!engine) return
    const items = []
    let colX = Infinity
    for (const { built } of builtRef.current.values()) {
      const el = labelRefs.current.get(built.id)
      if (el) {
        const p = engine.project(built.start, {})
        colX = Math.min(colX, p.x)
        items.push({ el, y: p.y, h: el._h || 34 })
      }
      if (!showStagesRef.current) continue
      built.stars.forEach((s, j) => {
        const sel = labelRefs.current.get(`${built.id}:${j}`)
        if (sel) placeLabel(sel, engine.project(s, {}), 'below')
      })
    }
    const ys = stackLabels(items, engine.size.h, 4)
    items.forEach((it, k) => placeLabel(it.el, { x: colX, y: ys[k] }, 'start'))
  }, [])

  const { engineRef, canvasRef } = useEngine({
    wrapRef,
    mode,
    onFallback,
    onFrame,
    onResize: (engine) => {
      rectRef.current = null
      const pad = padFor(engine.size.w, engine.size.h, cometsRef.current.length)
      const lead = pad.l + LEAD_X * (engine.size.w - pad.l - pad.r)
      wrapRef.current?.style.setProperty('--co-c3d-label-w', `${Math.round(lead - 30)}px`)
      sync(engine, cometsRef.current, true)
    },
    onDispose: () => {
      for (const v of builtRef.current.values()) v.built.dispose()
      builtRef.current = new Map()
    },
  })
  engineHolder.current = engineRef

  useEffect(() => {
    const engine = engineRef.current
    if (engine && engine.size.w > 1) sync(engine, comets)
  }, [comets, sync, engineRef])

  const highlight = useCallback(
    (id) => {
      if (hoverRef.current === id) return
      builtRef.current.get(hoverRef.current)?.built.setHover(false)
      hoverRef.current = id
      builtRef.current.get(id)?.built.setHover(true)
      engineRef.current?.renderOnce()
      setHover(id)
    },
    [engineRef],
  )

  const local = (e) => {
    const r = (rectRef.current ||= wrapRef.current.getBoundingClientRect())
    return { x: e.clientX - r.left, y: e.clientY - r.top, w: r.width, h: r.height }
  }
  const builtList = () => [...builtRef.current.values()].map((v) => v.built)

  const onPointerMove = (e) => {
    const engine = engineRef.current
    if (!engine) return
    const p = local(e)
    engine.setPointer((p.x / p.w) * 2 - 1, (p.y / p.h) * 2 - 1)
    if (e.target === canvasRef.current) highlight(hitTest(engine, builtList(), p.x, p.y)?.id ?? null)
  }
  const onPointerLeave = () => {
    rectRef.current = null
    engineRef.current?.setPointer(0, 0)
    highlight(null)
  }
  const onCanvasClick = (e) => {
    const engine = engineRef.current
    if (!engine) return
    const p = local(e)
    const hit = hitTest(engine, builtList(), p.x, p.y)
    if (hit) onOpen?.(hit.comet)
  }

  const setLabelRef = (key) => (el) => {
    if (el) labelRefs.current.set(key, el)
    else labelRefs.current.delete(key)
  }

  return (
    <div className="co-c3d" data-testid="comet-scene-3d">
      <div
        ref={wrapRef}
        className={`co-c3d-stage${hover ? ' is-hover' : ''}`}
        style={{ height: canvasHeight(comets.length) }}
        onPointerMove={onPointerMove}
        onPointerLeave={onPointerLeave}
        onClick={(e) => e.target === canvasRef.current && onCanvasClick(e)}
      >
        <div className="co-c3d-labels" aria-hidden="true">
          {comets.map((c) => {
            const head = c.stars[c.headIndex]
            return (
              <div key={c.id}>
                <div
                  ref={setLabelRef(c.id)}
                  className={`co-c3d-label co-c3d-label-${head.state}${hover === c.id ? ' is-hover' : ''}`}
                  data-testid="comet-label"
                  data-comet-id={c.id}
                  onClick={() => onOpen?.(c)}
                  onPointerEnter={() => highlight(c.id)}
                  title={`${c.title} — ${head.name} (${stateLabel(head.state)})`}
                >
                  <span className="co-c3d-label-title">{c.title}</span>
                  <span className="co-c3d-label-stage">
                    {head.name} · {stateLabel(head.state)}
                  </span>
                </div>
                {showStages &&
                  c.stars.map((s, j) =>
                    j === c.headIndex ? null : (
                      <div key={j} ref={setLabelRef(`${c.id}:${j}`)} className="co-c3d-stagename">
                        {s.name}
                      </div>
                    ),
                  )}
              </div>
            )
          })}
        </div>
      </div>
      <CometList comets={comets} overflow={overflow} onOpen={onOpen} onMore={onMore} onHighlight={highlight} />
    </div>
  )
}
