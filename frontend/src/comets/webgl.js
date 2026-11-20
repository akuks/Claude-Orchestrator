// "Can this browser do WebGL?" without importing three.js, so the 3D-vs-SVG
// decision never pulls three into the entry chunk. Cached: the probe creates
// a context, which is not free.
let cached

export function hasWebGL() {
  if (cached !== undefined) return cached
  cached = false
  try {
    if (typeof window === 'undefined' || typeof window.WebGLRenderingContext === 'undefined') return cached
    const canvas = document.createElement('canvas')
    const gl = canvas.getContext('webgl2') || canvas.getContext('webgl')
    cached = Boolean(gl)
    // free the probe context right away (browsers cap live contexts)
    gl?.getExtension?.('WEBGL_lose_context')?.loseContext()
  } catch {
    cached = false
  }
  return cached
}

// Tests only.
export function resetWebGLCache() {
  cached = undefined
}
