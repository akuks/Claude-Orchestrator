// Comet layout shared by the SVG renderer (CometSvg) and the 3D scene: each
// live item becomes a comet whose stars are its stages, placed along a gently
// curved path in a unit box (x, y in [0, 1], depth z in [-1, 1]). Pure and
// deterministic: the same items always give the same coordinates (the
// per-comet variation is seeded from the item id, never Math.random).

export const MAX_COMETS = 12

const X0 = 0.06
const X1 = 0.94
const Y_TOP = 0.12
const Y_BOTTOM = 0.88

// Where a run "stands": the stage in progress, or one stopped for a decision.
const HEAD_STATES = new Set(['current', 'waiting_approval', 'failed'])

// FNV-1a 32-bit hash of a string.
function hashString(s) {
  let h = 0x811c9dc5
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i)
    h = Math.imul(h, 0x01000193)
  }
  return h >>> 0
}

// mulberry32: small seeded PRNG returning floats in [0, 1).
function seeded(seed) {
  let a = seed >>> 0
  return () => {
    a = (a + 0x6d2b79f5) >>> 0
    let t = a
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

// Centre line (y) of comet i of n: the lanes split the vertical band evenly.
export function laneY(i, n) {
  return Y_TOP + ((Y_BOTTOM - Y_TOP) * (i + 0.5)) / Math.max(1, n)
}

const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v))
const round = (v) => Math.round(v * 10000) / 10000

// Index of the head star: the first current / approval-waiting / failed star,
// else the last done one, else the first.
export function headIndexOf(stars) {
  const at = stars.findIndex((s) => HEAD_STATES.has(s.state))
  if (at !== -1) return at
  for (let i = stars.length - 1; i >= 0; i--) if (stars[i].state === 'done') return i
  return 0
}

function starsFor(item) {
  const stages = Array.isArray(item.stages) ? item.stages : []
  // A thread that hasn't dispatched anything yet still shows as one live star.
  if (!stages.length) return [{ name: 'Starting', state: 'current', task_count: 0 }]
  return stages.map((s) => ({ name: s.name, state: s.state, task_count: s.task_count ?? 0 }))
}

// Over the cap, items needing attention win the visible slots: any failed
// stage, then waiting for approval, then in progress, then the rest.
function attention(item) {
  const states = new Set((item.stages || []).map((s) => s.state))
  if (states.has('failed')) return 0
  if (states.has('waiting_approval')) return 1
  if (states.has('current') || !states.size) return 2
  return 3
}

function pick(list, max) {
  if (list.length <= max) return list
  // Array.prototype.sort is stable, so input order holds within a group.
  return list
    .map((item, i) => ({ item, i, rank: attention(item) }))
    .sort((a, b) => a.rank - b.rank || a.i - b.i)
    .slice(0, max)
    .map((x) => x.item)
}

export function layoutComets(items, { max = MAX_COMETS } = {}) {
  const list = Array.isArray(items) ? items : []
  const shown = pick(list, Math.max(0, max))
  const n = shown.length
  // Fewer comets get a steeper climb; many keep to their lanes so they don't cross.
  const spread = n ? Math.min(1, 3 / n) : 1

  const comets = shown.map((item, i) => {
    const rand = seeded(hashString(String(item.id)))
    const lane = laneY(i, n)
    const rise = (0.12 + 0.14 * rand()) * spread
    const wobble = (0.02 + 0.04 * rand()) * spread
    const phase = rand() * Math.PI * 2
    const depth = rand() * 1.2 - 0.6
    const base = starsFor(item)
    const m = base.length
    const stars = base.map((s, j) => {
      const t = m > 1 ? j / (m - 1) : 0.5
      const x = X0 + (X1 - X0) * t
      const y = lane + rise * (0.5 - t) + wobble * Math.sin(Math.PI * 2 * t + phase)
      const z = depth + 0.3 * Math.sin(Math.PI * t + phase)
      return { name: s.name, state: s.state, taskCount: s.task_count, x: round(clamp(x, 0, 1)), y: round(clamp(y, 0.04, 0.96)), z: round(clamp(z, -1, 1)) }
    })
    return {
      id: item.id,
      kind: item.kind,
      title: item.title,
      status: item.status,
      stars,
      headIndex: headIndexOf(stars),
    }
  })

  return { comets, overflow: Math.max(0, list.length - n) }
}
