import { useEffect, useRef } from 'react'
import { createEngine } from './scene'

// Owns one engine for a canvas: creates it (or reports a fallback when WebGL
// can't start), keeps it sized to its container, runs it only while the tab
// is visible and the canvas is on screen, follows the light/night mode
// without recreating the renderer, and disposes everything on unmount.
export function useEngine({ wrapRef, mode, onFallback, onFrame, onResize, onDispose, dust, parallax }) {
  const engineRef = useRef(null)
  const canvasRef = useRef(null)
  const cb = useRef({})
  cb.current = { onFallback, onFrame, onResize, onDispose }

  useEffect(() => {
    const wrap = wrapRef.current
    if (!wrap) return undefined
    // A fresh canvas per mount: a canvas whose context was released can't be
    // reused (StrictMode mounts twice in development).
    const canvas = document.createElement('canvas')
    canvas.className = 'co-c3d-canvas'
    canvas.setAttribute('aria-hidden', 'true')
    wrap.prepend(canvas)
    let engine
    try {
      engine = createEngine(canvas, { mode, dust, parallax, onFrame: (t) => cb.current.onFrame?.(t) })
    } catch {
      canvas.remove()
      cb.current.onFallback?.()
      return undefined
    }
    engineRef.current = engine
    canvasRef.current = canvas

    let onScreen = true
    const update = () => engine.setRunning(onScreen && !document.hidden)
    const onVisibility = () => update()
    document.addEventListener('visibilitychange', onVisibility)
    const io =
      typeof IntersectionObserver !== 'undefined'
        ? new IntersectionObserver((entries) => {
            onScreen = entries[entries.length - 1].isIntersecting
            update()
          })
        : null
    io?.observe(wrap)

    let pending = 0
    const resize = () => {
      pending = 0
      const w = wrap.clientWidth
      const h = wrap.clientHeight
      if (!w || !h) return
      const { w: ow, h: oh } = engine.size
      if (ow === w && oh === h) return
      engine.setSize(w, h)
      cb.current.onResize?.(engine)
      engine.renderOnce()
    }
    const ro =
      typeof ResizeObserver !== 'undefined'
        ? new ResizeObserver(() => {
            if (!pending) pending = requestAnimationFrame(resize)
          })
        : null
    ro?.observe(wrap)
    resize()
    update()

    const lost = (e) => {
      e.preventDefault()
      cb.current.onFallback?.()
    }
    canvas.addEventListener('webglcontextlost', lost)

    return () => {
      canvas.removeEventListener('webglcontextlost', lost)
      document.removeEventListener('visibilitychange', onVisibility)
      io?.disconnect()
      ro?.disconnect()
      if (pending) cancelAnimationFrame(pending)
      cb.current.onDispose?.(engine)
      engineRef.current = null
      canvasRef.current = null
      engine.dispose()
      canvas.remove()
    }
    // the engine lives for the component's lifetime; mode follows below
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    const engine = engineRef.current
    if (!engine) return
    engine.setMode(mode)
    engine.renderOnce()
  }, [mode])

  return { engineRef, canvasRef }
}
