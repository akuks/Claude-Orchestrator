// Run-page timeline: one comet laid out left to right across a run's stages,
// in the same unit box as layoutComets() (x, y in [0, 1], depth z in [-1, 1]).
// A gentle arc and a little depth keep it from reading as a flat stepper.
// Shared by the 3D CometTimeline and its SVG fallback.
import { headIndexOf } from './layout'

const STATES = new Set(['done', 'current', 'next', 'failed', 'waiting_approval'])
const X0 = 0.07
const X1 = 0.93
const round = (v) => Math.round(v * 10000) / 10000

export function timelineComet(stages) {
  const list = Array.isArray(stages) ? stages : []
  if (!list.length) return { stars: [], headIndex: -1 }
  const m = list.length
  const stars = list.map((s, j) => {
    const t = m > 1 ? j / (m - 1) : 0.5
    return {
      name: String(s?.name ?? `Stage ${j + 1}`),
      state: STATES.has(s?.state) ? s.state : 'next',
      taskCount: s?.task_count ?? 0,
      x: round(X0 + (X1 - X0) * t),
      // a low arc: rises towards the middle, settles at the end
      y: round(0.56 - 0.16 * Math.sin(Math.PI * t) + 0.04 * (t - 0.5)),
      z: round(0.35 * Math.sin(Math.PI * 2 * t)),
    }
  })
  return { stars, headIndex: headIndexOf(stars) }
}
