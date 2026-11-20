// three.js helpers for the comet scene and the run timeline. Only src/three/*
// imports three, and those modules are only reached through React.lazy, so
// three stays out of the entry chunk.
//
// Everything is drawn with small ShaderMaterials (no textures, no lights):
//   aurora backdrop plane → particle field → trail glow → trail core → stars → head spark
// Text never goes through WebGL: callers position HTML labels with project().
// Colours are written as sRGB straight to the canvas (no colour management),
// so they match the CSS tokens exactly.
import {
  AdditiveBlending,
  BufferAttribute,
  BufferGeometry,
  CatmullRomCurve3,
  Mesh,
  NormalBlending,
  PerspectiveCamera,
  PlaneGeometry,
  Points,
  Scene,
  ShaderMaterial,
  TubeGeometry,
  Vector3,
  WebGLRenderer,
} from 'three'
import { modeTokens, palette } from '../theme/tokens'

const PX = 40 // CSS pixels per world unit on the z = 0 plane
const FOV = 32
const DEPTH = 1.3 // world units for layout z = ±1 (more skews lanes too much)
const TAN = Math.tan(((FOV / 2) * Math.PI) / 180)

const KIND = { done: 0, next: 1, current: 2, failed: 3, waiting_approval: 4 }
const STAR_SIZE = { done: 26, next: 22, current: 78, failed: 30, waiting_approval: 40 } // CSS px

export function rgb(hex) {
  const h = hex.replace('#', '')
  const n = parseInt(h.length === 3 ? h.replace(/./g, '$&$&') : h, 16)
  return new Vector3(((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255)
}

// Per-mode colours for the canvas (CSS variables can't reach shaders).
export function sceneColors(mode) {
  const t = modeTokens(mode)
  const night = mode === 'night'
  return {
    night,
    amber: rgb(palette.amber),
    orange: rgb(palette.orange),
    rose: rgb(palette.rose),
    done: rgb(t.status.success),
    failed: rgb(t.status.danger),
    wait: rgb(t.status.warning),
    next: rgb(night ? '#a8929f' : '#c4a9b8'),
    fill: rgb(t.surfaceSolid),
    flow: rgb(night ? '#fff4e6' : '#ffffff'),
    sparkCore: rgb(night ? '#fff7ed' : '#ffffff'),
    sparkHalo: rgb(night ? palette.orange : palette.amber),
    sparkFalloff: night ? 1.8 : 2.8,
    dust: night ? ['#f59e0b', '#f97316', '#e11d48', '#fbeff3'] : ['#f59e0b', '#f97316', '#e11d48'],
    dustAlpha: night ? 0.75 : 0.42,
    auroraAlpha: night ? 0.16 : 0.12,
    trailGlow: night ? 0.34 : 0.17,
  }
}

// Additive glow is invisible on a white page, so day mode blends normally.
function glowBlending(colors) {
  return colors.night ? AdditiveBlending : NormalBlending
}

// FNV-1a: stable per-comet phase so sparks don't move in lockstep.
function hash01(s) {
  let h = 0x811c9dc5
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i)
    h = Math.imul(h, 0x01000193)
  }
  return (h >>> 0) / 4294967296
}

// ---------------------------------------------------------------- shaders
const FIELD_VS = /* glsl */ `
attribute float aSize; attribute float aPhase; attribute vec3 aColor;
uniform float uTime; uniform float uPR; uniform float uDist; uniform float uAlpha;
varying vec3 vColor; varying float vA;
void main() {
  vec3 p = position;
  p.x += sin(uTime * 0.05 + aPhase) * 0.5;
  p.y += cos(uTime * 0.04 + aPhase * 1.3) * 0.35;
  vec4 mv = modelViewMatrix * vec4(p, 1.0);
  gl_Position = projectionMatrix * mv;
  gl_PointSize = aSize * uPR * (uDist / -mv.z);
  vColor = aColor;
  vA = uAlpha * (0.45 + 0.55 * (0.5 + 0.5 * sin(uTime * 1.1 + aPhase * 6.0)));
}`
const FIELD_FS = /* glsl */ `
varying vec3 vColor; varying float vA;
void main() {
  float d = length(gl_PointCoord - 0.5) * 2.0;
  if (d > 1.0) discard;
  float a = pow(1.0 - d, 2.0);
  gl_FragColor = vec4(vColor, a * vA);
}`

const AURORA_VS = /* glsl */ `
varying vec2 vUv;
void main() { vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`
const AURORA_FS = /* glsl */ `
uniform float uTime; uniform float uAlpha; uniform vec3 uA; uniform vec3 uB; uniform vec3 uC;
varying vec2 vUv;
void main() {
  float y1 = vUv.y - 0.70 - 0.07 * sin(vUv.x * 5.0 + uTime * 0.16);
  float y2 = vUv.y - 0.30 - 0.05 * sin(vUv.x * 4.0 - uTime * 0.12 + 1.7);
  float b = exp(-y1 * y1 * 60.0) + 0.65 * exp(-y2 * y2 * 90.0);
  vec3 col = mix(mix(uA, uB, smoothstep(0.0, 0.55, vUv.x)), uC, smoothstep(0.45, 1.0, vUv.x));
  float edge = smoothstep(0.0, 0.25, vUv.x) * smoothstep(1.0, 0.75, vUv.x);
  gl_FragColor = vec4(col, b * edge * uAlpha);
}`

const TRAIL_VS = /* glsl */ `
varying float vU; varying vec3 vN; varying vec3 vV;
void main() {
  vU = uv.x;
  vec4 mv = modelViewMatrix * vec4(position, 1.0);
  vN = normalize(normalMatrix * normal);
  vV = normalize(-mv.xyz);
  gl_Position = projectionMatrix * mv;
}`
const TRAIL_FS = /* glsl */ `
uniform float uTime; uniform float uHeadU; uniform float uAlpha; uniform float uHover; uniform float uGlow;
uniform vec3 uC0; uniform vec3 uC1; uniform vec3 uC2; uniform vec3 uFlow; uniform float uPhase;
varying float vU; varying vec3 vN; varying vec3 vV;
void main() {
  float facing = abs(dot(normalize(vN), normalize(vV)));
  float h = max(uHeadU, 0.001);
  float done = 1.0 - smoothstep(uHeadU, uHeadU + 0.035, vU);
  vec3 col = vU <= uHeadU
    ? mix(uC0, uC1, smoothstep(0.0, 1.0, vU / h))
    : mix(uC1, uC2, smoothstep(0.0, 0.35, (vU - uHeadU) / max(1.0 - uHeadU, 0.001)));
  float alpha = mix(0.26, 1.0, done) * smoothstep(0.0, 0.06, vU);
  if (uGlow > 0.5) {
    alpha *= pow(facing, 2.6) * (1.0 + uHover * 0.8);
  } else {
    float flow = pow(0.5 + 0.5 * sin(vU * 46.0 - uTime * 3.2 + uPhase), 8.0) * done;
    col = mix(col, uFlow, flow * 0.7);
    alpha *= smoothstep(0.0, 0.45, facing);
  }
  gl_FragColor = vec4(col, alpha * uAlpha);
}`

const STAR_VS = /* glsl */ `
attribute vec3 aColor; attribute float aSize; attribute float aKind; attribute float aPhase;
uniform float uPR; uniform float uDist; uniform float uHover;
varying vec3 vColor; varying float vKind; varying float vPhase;
void main() {
  vec4 mv = modelViewMatrix * vec4(position, 1.0);
  gl_Position = projectionMatrix * mv;
  gl_PointSize = aSize * (1.0 + uHover * 0.14) * uPR * (uDist / -mv.z);
  vColor = aColor; vKind = aKind; vPhase = aPhase;
}`
const STAR_FS = /* glsl */ `
uniform float uTime; uniform float uAlpha; uniform vec3 uFill; uniform float uNight;
varying vec3 vColor; varying float vKind; varying float vPhase;
void main() {
  float d = length(gl_PointCoord - 0.5) * 2.0;
  if (d > 1.0) discard;
  vec3 col = vColor;
  float a = 0.0;
  if (vKind < 0.5 || (vKind > 2.5 && vKind < 3.5)) {
    // done / failed: solid core with a soft glow
    float core = 1.0 - smoothstep(0.30, 0.36, d);
    float glow = pow(1.0 - d, 2.4) * 0.5;
    col = mix(col, vec3(1.0), core * 0.25 * (1.0 - d * 3.0));
    a = max(core, glow);
  } else if (vKind < 1.5) {
    // next: hollow ring, filled with the surface colour
    float ring = smoothstep(0.30, 0.36, d) * (1.0 - smoothstep(0.50, 0.56, d));
    float fill = 1.0 - smoothstep(0.34, 0.38, d);
    col = mix(uFill, vColor, ring);
    a = max(ring * 0.95, fill * (1.0 - uNight * 0.6));
  } else if (vKind < 2.5) {
    // current: hot core, breathing halo and an expanding ripple
    float pulse = 0.5 + 0.5 * sin(uTime * 2.4 + vPhase);
    float core = 1.0 - smoothstep(0.15, 0.19, d);
    float halo = pow(max(0.0, 1.0 - d / (0.62 + 0.30 * pulse)), 2.2) * (0.62 - 0.22 * pulse);
    float f = fract(uTime * 0.42 + vPhase * 0.159);
    float r = 0.2 + 0.75 * f;
    float ripple = (1.0 - smoothstep(0.0, 0.035, abs(d - r))) * (1.0 - f) * 0.55;
    col = mix(col, vec3(1.0, 0.97, 0.9), core * (1.0 - d * 4.0) * (0.35 + uNight * 0.5));
    a = max(core, max(halo, ripple));
  } else {
    // waiting for approval: amber ring, pale fill, slow beacon glow
    float pulse = 0.5 + 0.5 * sin(uTime * 1.3 + vPhase);
    float ring = smoothstep(0.26, 0.31, d) * (1.0 - smoothstep(0.40, 0.45, d));
    float fill = 1.0 - smoothstep(0.28, 0.32, d);
    float glow = pow(max(0.0, 1.0 - d), 2.0) * (0.18 + 0.3 * pulse);
    col = mix(mix(uFill, vColor, 0.22 + uNight * 0.2), vColor, ring);
    a = max(max(ring, fill * 0.95), glow);
    if (fill < 0.5 && ring < 0.5) col = vColor;
  }
  gl_FragColor = vec4(col, a * uAlpha);
}`

const SPARK_VS = /* glsl */ `
attribute float aSize; attribute float aAlpha;
uniform float uPR; uniform float uDist;
varying float vA;
void main() {
  vec4 mv = modelViewMatrix * vec4(position, 1.0);
  gl_Position = projectionMatrix * mv;
  gl_PointSize = aSize * uPR * (uDist / -mv.z);
  vA = aAlpha;
}`
const SPARK_FS = /* glsl */ `
uniform vec3 uColor; uniform vec3 uCore; uniform float uAlpha; uniform float uFalloff;
varying float vA;
void main() {
  float d = length(gl_PointCoord - 0.5) * 2.0;
  if (d > 1.0) discard;
  float a = pow(1.0 - d, uFalloff);
  vec3 col = mix(uColor, uCore, pow(1.0 - d, 4.0));
  gl_FragColor = vec4(col, a * vA * uAlpha);
}`

function shader(vertexShader, fragmentShader, uniforms, blending = NormalBlending) {
  return new ShaderMaterial({
    vertexShader,
    fragmentShader,
    uniforms,
    transparent: true,
    depthWrite: false,
    depthTest: false,
    blending,
  })
}

// ---------------------------------------------------------------- engine
// createEngine(canvas, opts) → a renderer + camera sized in CSS pixels, a
// particle field, an aurora backdrop, pointer parallax and a pausable loop.
export function createEngine(canvas, { mode = 'light', dust = 340, onFrame, parallax = 1 } = {}) {
  const renderer = new WebGLRenderer({ canvas, antialias: true, alpha: true, powerPreference: 'low-power' })
  renderer.setClearColor(0x000000, 0)
  const pr = Math.min(window.devicePixelRatio || 1, 2)
  renderer.setPixelRatio(pr)

  const scene = new Scene()
  const camera = new PerspectiveCamera(FOV, 1, 0.1, 200)
  const shared = { uTime: { value: 0 }, uPR: { value: pr }, uDist: { value: 20 } }
  let colors = sceneColors(mode)
  let size = { w: 1, h: 1, W: 1, H: 1, dist: 20 }
  const updaters = new Set()
  const glowMaterials = new Set() // materials that switch blending with the mode
  const modeListeners = new Set()

  // aurora backdrop
  const auroraGeo = new PlaneGeometry(1, 1)
  const auroraMat = shader(AURORA_VS, AURORA_FS, {
    uTime: shared.uTime,
    uAlpha: { value: colors.auroraAlpha },
    uA: { value: colors.amber },
    uB: { value: colors.orange },
    uC: { value: colors.rose },
  })
  const aurora = new Mesh(auroraGeo, auroraMat)
  aurora.position.z = -9
  aurora.renderOrder = 0
  scene.add(aurora)

  // particle field
  const fieldGeo = new BufferGeometry()
  const fieldMat = shader(FIELD_VS, FIELD_FS, { ...shared, uAlpha: { value: colors.dustAlpha } })
  const field = new Points(fieldGeo, fieldMat)
  field.renderOrder = 1
  field.frustumCulled = false
  scene.add(field)
  glowMaterials.add(fieldMat)
  let seed = 7
  const rand = () => {
    seed = (seed * 16807) % 2147483647
    return seed / 2147483647
  }
  function fillField() {
    const pos = new Float32Array(dust * 3)
    const col = new Float32Array(dust * 3)
    const sz = new Float32Array(dust)
    const ph = new Float32Array(dust)
    seed = 7
    for (let i = 0; i < dust; i++) {
      const z = -10 + rand() * 12
      const spread = 1 + (size.dist - z) / size.dist // farther = wider, so the view stays filled
      pos[i * 3] = (rand() - 0.5) * size.W * spread * 1.1
      pos[i * 3 + 1] = (rand() - 0.5) * size.H * spread * 1.1
      pos[i * 3 + 2] = z
      const c = rgb(colors.dust[Math.floor(rand() * colors.dust.length)])
      col.set([c.x, c.y, c.z], i * 3)
      sz[i] = 1.4 + rand() * rand() * 4.2
      ph[i] = rand() * Math.PI * 2
    }
    fieldGeo.setAttribute('position', new BufferAttribute(pos, 3))
    fieldGeo.setAttribute('aColor', new BufferAttribute(col, 3))
    fieldGeo.setAttribute('aSize', new BufferAttribute(sz, 1))
    fieldGeo.setAttribute('aPhase', new BufferAttribute(ph, 1))
  }

  function setSize(w, h) {
    w = Math.max(1, Math.round(w))
    h = Math.max(1, Math.round(h))
    const W = w / PX
    const H = h / PX
    const dist = H / 2 / TAN
    size = { w, h, W, H, dist }
    renderer.setSize(w, h, false)
    camera.aspect = w / h
    camera.position.set(0, 0, dist)
    camera.lookAt(0, 0, 0)
    camera.updateProjectionMatrix()
    shared.uDist.value = dist
    aurora.scale.set(W * 1.9, H * 1.9, 1)
    fillField()
    needsFrame = true
  }

  // layout box ([0,1]² + z in [-1,1]) → world, inside CSS-pixel padding
  function toWorld(p, pad = {}) {
    const { l = 28, r = 28, t = 26, b = 26 } = pad
    const x0 = -size.W / 2 + l / PX
    const x1 = size.W / 2 - r / PX
    const y0 = size.H / 2 - t / PX
    const y1 = -size.H / 2 + b / PX
    return new Vector3(x0 + (x1 - x0) * p.x, y0 + (y1 - y0) * p.y, (p.z || 0) * DEPTH)
  }

  const tmp = new Vector3()
  // world → CSS pixels relative to the canvas
  function project(v, out = {}) {
    tmp.copy(v).project(camera)
    out.x = ((tmp.x + 1) / 2) * size.w
    out.y = ((1 - tmp.y) / 2) * size.h
    return out
  }

  function setMode(m) {
    colors = sceneColors(m)
    auroraMat.uniforms.uAlpha.value = colors.auroraAlpha
    fieldMat.uniforms.uAlpha.value = colors.dustAlpha
    for (const mat of glowMaterials) mat.blending = glowBlending(colors)
    fillField()
    for (const fn of modeListeners) fn(colors)
    needsFrame = true
  }
  for (const mat of glowMaterials) mat.blending = glowBlending(colors)

  // pointer parallax (eased) plus a slow idle sway
  const target = { x: 0, y: 0 }
  const cam = { x: 0, y: 0 }
  function setPointer(nx, ny) {
    target.x = Math.max(-1, Math.min(1, nx))
    target.y = Math.max(-1, Math.min(1, ny))
  }

  let raf = 0
  let running = false
  let needsFrame = true
  let last = 0
  let time = 0
  function frame(now) {
    raf = 0
    const dt = last ? Math.min(0.1, (now - last) / 1000) : 1 / 60
    last = now
    time += dt
    shared.uTime.value = time
    const k = 1 - Math.exp(-dt * 3.2)
    cam.x += (target.x - cam.x) * k
    cam.y += (target.y - cam.y) * k
    const swayX = Math.sin(time * 0.13) * 0.22
    const swayY = Math.cos(time * 0.1) * 0.12
    camera.position.set((cam.x + swayX) * 1.5 * parallax, (-cam.y + swayY) * 0.9 * parallax, size.dist)
    camera.lookAt(0, 0, 0)
    for (const fn of updaters) fn(time, dt)
    renderer.render(scene, camera)
    onFrame?.(time)
    needsFrame = false
    if (running) raf = requestAnimationFrame(frame)
  }
  function setRunning(on) {
    if (on === running) return
    running = on
    if (on) {
      last = 0
      if (!raf) raf = requestAnimationFrame(frame)
    } else if (raf) {
      cancelAnimationFrame(raf)
      raf = 0
    }
  }
  // one frame even while paused (e.g. after a resize or data change)
  function renderOnce() {
    if (!running && !raf && needsFrame) raf = requestAnimationFrame(frame)
  }

  function dispose() {
    setRunning(false)
    if (raf) cancelAnimationFrame(raf)
    updaters.clear()
    modeListeners.clear()
    auroraGeo.dispose()
    auroraMat.dispose()
    fieldGeo.dispose()
    fieldMat.dispose()
    renderer.dispose()
    renderer.forceContextLoss()
  }

  return {
    renderer,
    scene,
    camera,
    shared,
    get colors() {
      return colors
    },
    get size() {
      return size
    },
    setSize,
    setMode,
    toWorld,
    project,
    setPointer,
    setRunning,
    renderOnce,
    markDirty: () => {
      needsFrame = true
    },
    addUpdater: (fn) => updaters.add(fn) && (() => updaters.delete(fn)),
    onMode: (fn) => modeListeners.add(fn) && (() => modeListeners.delete(fn)),
    trackGlow: (mat) => {
      glowMaterials.add(mat)
      mat.blending = glowBlending(colors)
      return () => glowMaterials.delete(mat)
    },
    dispose,
  }
}

// ---------------------------------------------------------------- comet
const easeInOut = (p) => (p < 0.5 ? 2 * p * p : 1 - Math.pow(-2 * p + 2, 2) / 2)
const TAIL = 18

// buildComet(engine, comet, pad) → one comet: glow + core trail tubes along a
// CatmullRomCurve3 through its stars, the star sprites, and a head spark that
// keeps travelling up the finished part of the path to where the work is.
// `leadIn` (layout coords) optionally starts the tail at a given point, e.g.
// next to the comet's label, so each comet visibly flies out of its label.
export function buildComet(engine, comet, pad, { alpha = 1, leadIn } = {}) {
  const group = []
  const stars = comet.stars.map((s) => engine.toWorld(s, pad))
  const lead = leadIn
    ? engine.toWorld({ ...leadIn, z: leadIn.z ?? comet.stars[0].z }, pad)
    : stars.length > 1
      ? null
      : stars[0].clone().add(new Vector3(-3.2, -0.5, -0.4))
  const pts = lead ? [lead, ...stars] : stars
  const first = pts.length - stars.length // index of star 0 on the curve
  const curve = new CatmullRomCurve3(pts, false, 'centripetal')
  const length = curve.getLength()

  // arc-length position (0..1) of the head star on the curve
  const lengths = curve.getLengths(200)
  const headT = ((first + comet.headIndex) / (pts.length - 1)) * 200
  const headU = lengths[Math.round(headT)] / length
  // a fully finished comet glows end to end
  const allDone = comet.stars.every((s) => s.state === 'done')
  const litU = allDone ? 1 : headU
  const depthAlpha = alpha * (0.78 + 0.22 * ((comet.stars[0]?.z ?? 0) + 1) / 2)
  const phase = hash01(String(comet.id))
  let colors = engine.colors

  const segs = Math.max(48, Math.round(length * 6))
  const trailUniforms = (glow) => ({
    uTime: engine.shared.uTime,
    uHeadU: { value: litU },
    uAlpha: { value: depthAlpha * (glow ? colors.trailGlow : 1) },
    uHover: { value: 0 },
    uGlow: { value: glow ? 1 : 0 },
    uC0: { value: colors.amber },
    uC1: { value: colors.orange },
    uC2: { value: colors.rose },
    uFlow: { value: colors.flow },
    uPhase: { value: phase * 6.28 },
  })
  const glowGeo = new TubeGeometry(curve, segs, 0.32, 10, false)
  const glowMat = shader(TRAIL_VS, TRAIL_FS, trailUniforms(true))
  const glowMesh = new Mesh(glowGeo, glowMat)
  glowMesh.renderOrder = 2
  const coreGeo = new TubeGeometry(curve, segs, 0.065, 8, false)
  const coreMat = shader(TRAIL_VS, TRAIL_FS, trailUniforms(false))
  const coreMesh = new Mesh(coreGeo, coreMat)
  coreMesh.renderOrder = 3
  group.push(glowMesh, coreMesh)

  // stars
  const n = stars.length
  const starGeo = new BufferGeometry()
  const pos = new Float32Array(n * 3)
  stars.forEach((v, i) => pos.set([v.x, v.y, v.z], i * 3))
  starGeo.setAttribute('position', new BufferAttribute(pos, 3))
  const kinds = new Float32Array(comet.stars.map((s) => KIND[s.state] ?? KIND.next))
  starGeo.setAttribute('aKind', new BufferAttribute(kinds, 1))
  starGeo.setAttribute('aSize', new BufferAttribute(new Float32Array(comet.stars.map((s) => STAR_SIZE[s.state] ?? STAR_SIZE.next)), 1))
  starGeo.setAttribute('aPhase', new BufferAttribute(new Float32Array(comet.stars.map((_, i) => phase * 6.28 + i * 1.7)), 1))
  const starColor = new BufferAttribute(new Float32Array(n * 3), 3)
  starGeo.setAttribute('aColor', starColor)
  const starMat = shader(STAR_VS, STAR_FS, {
    ...engine.shared,
    uAlpha: { value: Math.min(1, depthAlpha + 0.1) },
    uHover: { value: 0 },
    uFill: { value: colors.fill },
    uNight: { value: colors.night ? 1 : 0 },
  })
  const starPoints = new Points(starGeo, starMat)
  starPoints.renderOrder = 4
  starPoints.frustumCulled = false
  group.push(starPoints)

  // head spark + tail
  const sparkGeo = new BufferGeometry()
  const sparkPos = new BufferAttribute(new Float32Array(TAIL * 3), 3)
  const sparkSize = new BufferAttribute(new Float32Array(TAIL), 1)
  const sparkAlpha = new BufferAttribute(new Float32Array(TAIL), 1)
  sparkGeo.setAttribute('position', sparkPos)
  sparkGeo.setAttribute('aSize', sparkSize)
  sparkGeo.setAttribute('aAlpha', sparkAlpha)
  const sparkMat = shader(SPARK_VS, SPARK_FS, {
    uPR: engine.shared.uPR,
    uDist: engine.shared.uDist,
    uColor: { value: colors.sparkHalo },
    uCore: { value: colors.sparkCore },
    uFalloff: { value: colors.sparkFalloff },
    uAlpha: { value: depthAlpha },
  })
  const spark = new Points(sparkGeo, sparkMat)
  spark.renderOrder = 5
  spark.frustumCulled = false
  const runLen = litU * length
  const hasSpark = runLen > 0.8
  if (hasSpark) group.push(spark)

  const untrack = [engine.trackGlow(glowMat), engine.trackGlow(sparkMat)]

  function paint(c) {
    colors = c
    for (const m of [glowMat, coreMat]) {
      m.uniforms.uC0.value = c.amber
      m.uniforms.uC1.value = c.orange
      m.uniforms.uC2.value = c.rose
      m.uniforms.uFlow.value = c.flow
    }
    glowMat.uniforms.uAlpha.value = depthAlpha * c.trailGlow
    starMat.uniforms.uFill.value = c.fill
    starMat.uniforms.uNight.value = c.night ? 1 : 0
    sparkMat.uniforms.uColor.value = c.sparkHalo
    sparkMat.uniforms.uCore.value = c.sparkCore
    sparkMat.uniforms.uFalloff.value = c.sparkFalloff
    const byState = { done: c.done, next: c.next, current: c.orange, failed: c.failed, waiting_approval: c.wait }
    comet.stars.forEach((s, i) => {
      const v = byState[s.state] || c.next
      starColor.setXYZ(i, v.x, v.y, v.z)
    })
    starColor.needsUpdate = true
  }
  paint(colors)
  const offMode = engine.onMode(paint)

  // spark: travel the lit path (eased), linger, fade into the head, repeat
  const travel = Math.min(4.2, Math.max(1.8, runLen * 0.16))
  const cycle = travel + 1.1
  const du = 0.2 / length
  const p = new Vector3()
  function update(time) {
    if (!hasSpark) return
    const local = (time + phase * cycle) % cycle
    const prog = Math.min(1, local / travel)
    const u = easeInOut(prog) * litU
    const fadeIn = Math.min(1, local / 0.35)
    const fadeOut = local > travel ? Math.max(0, 1 - (local - travel) / 0.9) : 1
    for (let i = 0; i < TAIL; i++) {
      const ui = Math.max(0, u - i * du * (0.6 + prog))
      curve.getPointAt(Math.min(1, ui), p)
      sparkPos.setXYZ(i, p.x, p.y, p.z)
      const k = 1 - i / TAIL
      sparkSize.setX(i, i === 0 ? 30 : 6 + 14 * k * k)
      sparkAlpha.setX(i, (i === 0 ? 1 : 0.75 * k * k) * fadeIn * fadeOut * (ui > 0 || i === 0 ? 1 : 0))
    }
    sparkPos.needsUpdate = true
    sparkSize.needsUpdate = true
    sparkAlpha.needsUpdate = true
  }
  const offUpdate = engine.addUpdater(update)
  update(0)

  for (const obj of group) engine.scene.add(obj)

  // screen-space samples of the path, for hover / click hit testing
  const samples = curve.getSpacedPoints(Math.max(16, Math.round(length * 2)))

  return {
    id: comet.id,
    comet,
    stars,
    head: stars[Math.min(comet.headIndex, n - 1)],
    start: pts[0],
    samples,
    setHover(on) {
      const v = on ? 1 : 0
      glowMat.uniforms.uHover.value = v
      coreMat.uniforms.uHover.value = v
      starMat.uniforms.uHover.value = v
      engine.markDirty()
    },
    dispose() {
      offUpdate()
      offMode()
      untrack.forEach((f) => f())
      for (const obj of group) engine.scene.remove(obj)
      for (const g of [glowGeo, coreGeo, starGeo, sparkGeo]) g.dispose()
      for (const m of [glowMat, coreMat, starMat, sparkMat]) m.dispose()
    },
  }
}

// Pointer hit test in CSS pixels: the head star (generous) or anywhere near
// the trail. Returns the nearest built comet or null.
export function hitTest(engine, built, x, y) {
  let best = null
  let bestD = Infinity
  const a = {}
  const b = {}
  for (const c of built) {
    engine.project(c.head, a)
    let d = Math.hypot(a.x - x, a.y - y) - 12 // favour heads
    for (let i = 0; i < c.samples.length - 1; i++) {
      engine.project(c.samples[i], a)
      engine.project(c.samples[i + 1], b)
      d = Math.min(d, segDist(x, y, a, b))
    }
    if (d < bestD) {
      bestD = d
      best = c
    }
  }
  return bestD <= 12 ? best : null
}

function segDist(px, py, a, b) {
  const dx = b.x - a.x
  const dy = b.y - a.y
  const L = dx * dx + dy * dy
  const t = L ? Math.max(0, Math.min(1, ((px - a.x) * dx + (py - a.y) * dy) / L)) : 0
  return Math.hypot(px - (a.x + t * dx), py - (a.y + t * dy))
}

// A stable key for a comet's look: rebuild only when this changes.
export function cometKey(c) {
  return `${c.id}|${c.headIndex}|${c.stars.map((s) => `${s.state}:${s.x},${s.y},${s.z}`).join(';')}`
}
