import { useId } from 'react'
import { palette } from '../theme/tokens'
import './comets.css'

// Static/2D rendering of layoutComets(): the reduced-motion / no-WebGL
// fallback for the Home scene, and (compact) the mini comet on project cards.
// Each comet is a curved trail through its stage stars: done = lit, current =
// glowing pulse, next = hollow, failed = red, waiting_approval = amber + lock.
// Motion is CSS-only and switched off by the reduce-motion settings.

const W = 640
// Project cards are ~260-340px wide: a narrower box keeps the mini comet at
// roughly 1:1 instead of shrinking a full-width scene to a hairline.
const W_COMPACT = 300
const PAD_X = 28

const STATE_LABEL = {
  done: 'done',
  current: 'in progress',
  next: 'up next',
  failed: 'failed',
  waiting_approval: 'waiting for approval',
}

export function stateLabel(state) {
  return STATE_LABEL[state] || state
}

function truncate(s, n) {
  const t = String(s ?? '')
  return t.length > n ? `${t.slice(0, n - 1)}…` : t
}

// Smooth path through points (Catmull-Rom → cubic Bézier).
export function smoothPath(pts) {
  if (!pts.length) return ''
  if (pts.length === 1) return `M${pts[0][0]} ${pts[0][1]}`
  let d = `M${pts[0][0]} ${pts[0][1]}`
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[i - 1] || pts[i]
    const p1 = pts[i]
    const p2 = pts[i + 1]
    const p3 = pts[i + 2] || p2
    const c1 = [p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6]
    const c2 = [p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6]
    d += ` C${r(c1[0])} ${r(c1[1])}, ${r(c2[0])} ${r(c2[1])}, ${r(p2[0])} ${r(p2[1])}`
  }
  return d
}
const r = (v) => Math.round(v * 10) / 10

function heightFor(n, compact) {
  if (compact) return 56
  return Math.max(240, Math.min(n, 12) * 54 + 60)
}

export function Star({ star, cx, cy, isHead, compact }) {
  const k = compact ? 0.7 : 1
  const depth = 1 + star.z * 0.12 // nearer stars are a touch larger
  const title = `${star.name}: ${stateLabel(star.state)}${star.taskCount ? ` · ${star.taskCount} task${star.taskCount === 1 ? '' : 's'}` : ''}`
  const base = { cx, cy }
  let body
  switch (star.state) {
    case 'done':
      body = <circle {...base} r={6 * k * depth} className="co-star-done" />
      break
    case 'failed':
      body = <circle {...base} r={7 * k * depth} className="co-star-failed" />
      break
    case 'waiting_approval':
      body = (
        <>
          <circle {...base} r={9 * k * depth} className="co-star-wait" />
          {!compact && (
            <text x={cx} y={cy + 3.5} textAnchor="middle" fontSize="9" aria-hidden="true">
              🔒
            </text>
          )}
        </>
      )
      break
    case 'current':
      body = (
        <>
          <circle {...base} r={16 * k * depth} className="co-star-halo" />
          <circle {...base} r={9 * k * depth} className="co-star-current" />
        </>
      )
      break
    default:
      body = <circle {...base} r={6 * k * depth} className="co-star-next" />
  }
  return (
    <g className={`co-star${isHead ? ' co-star-head' : ''}`} data-state={star.state}>
      <title>{title}</title>
      {body}
    </g>
  )
}

export default function CometSvg({ comets = [], overflow = 0, onOpen, onMore, compact = false }) {
  const uid = useId().replace(/[^a-zA-Z0-9_-]/g, '')
  const H = heightFor(comets.length, compact)
  const padTop = compact ? 12 : 30
  const padBottom = compact ? 12 : 26
  const w = compact ? W_COMPACT : W
  const padX = compact ? 14 : PAD_X
  const px = (x) => padX + x * (w - 2 * padX)
  const py = (y) => padTop + y * (H - padTop - padBottom)
  const showStageNames = !compact && comets.length <= 3

  return (
    <div className={`co-comets${compact ? ' co-comets-compact' : ''}`} data-testid="comet-svg">
      <svg
        viewBox={`0 0 ${w} ${H}`}
        width="100%"
        preserveAspectRatio="xMidYMid meet"
        role="img"
        aria-label={
          comets.length
            ? `${comets.length} live workflow${comets.length === 1 ? '' : 's'}`
            : 'No live workflows'
        }
        className="co-comets-svg"
      >
        <defs>
          {comets.map((c, i) => {
            const pts = c.stars
            const x0 = px(pts[0].x)
            const x1 = px(pts[pts.length - 1].x)
            const span = Math.max(1, x1 - x0)
            const head = (px(pts[c.headIndex].x) - x0) / span
            return (
              <linearGradient
                key={c.id}
                id={`${uid}-g${i}`}
                gradientUnits="userSpaceOnUse"
                x1={x0}
                y1="0"
                x2={x0 + span}
                y2="0"
              >
                <stop offset="0" stopColor={palette.amber} />
                <stop offset={Math.max(0, Math.min(1, head))} stopColor={palette.orange} />
                <stop offset={Math.min(1, head + 0.08)} stopColor={palette.rose} stopOpacity=".55" />
                <stop offset="1" stopColor={palette.rose} stopOpacity=".12" />
              </linearGradient>
            )
          })}
        </defs>

        {comets.map((c, i) => {
          const pts = c.stars.map((s) => [px(s.x), py(s.y)])
          // A one-star comet gets a short tail leading into it.
          const trail = pts.length > 1 ? pts : [[pts[0][0] - (compact ? 30 : 60), pts[0][1] + 10], pts[0]]
          const d = smoothPath(trail)
          const head = c.stars[c.headIndex]
          const [hx, hy] = pts[c.headIndex]
          const label = `${truncate(c.title, compact ? 24 : 34)} · ${head.name}`
          const open = () => onOpen?.(c)
          return (
            <g
              key={c.id}
              className={`co-comet${onOpen ? ' co-comet-link' : ''}`}
              data-testid="comet"
              data-comet-id={c.id}
              onClick={onOpen ? open : undefined}
            >
              <title>{`${c.title} — ${head.name} (${stateLabel(head.state)})`}</title>
              {/* wide invisible stroke: an easier click target */}
              <path d={d} className="co-comet-hit" />
              <path d={d} className="co-comet-trail" stroke={`url(#${uid}-g${i})`} strokeWidth={compact ? 3 : 4} />
              <path d={d} className="co-comet-flow" />
              {c.stars.map((s, j) => (
                <Star
                  key={`${s.name}-${j}`}
                  star={s}
                  cx={pts[j][0]}
                  cy={pts[j][1]}
                  isHead={j === c.headIndex}
                  compact={compact}
                />
              ))}
              {showStageNames &&
                c.stars.map((s, j) =>
                  j === c.headIndex ? null : (
                    <text key={`n${j}`} x={pts[j][0]} y={pts[j][1] + 22} textAnchor="middle" className="co-comet-stage">
                      {truncate(s.name, 16)}
                    </text>
                  ),
                )}
              {!compact && (
                // Beside the head (left of it near the right edge), just above
                // the trail, so labels stay in their own comet's lane.
                <text
                  x={head.x > 0.6 ? hx - 18 : hx + 18}
                  y={hy - 9}
                  textAnchor={head.x > 0.6 ? 'end' : 'start'}
                  className={`co-comet-label co-comet-label-${head.state}`}
                >
                  {label}
                </text>
              )}
            </g>
          )
        })}
      </svg>

      {!compact && <CometList comets={comets} overflow={overflow} onOpen={onOpen} onMore={onMore} />}
    </div>
  )
}

// Keyboard-reachable list of the comets (and the "+N more") shown under the
// SVG scene and under the 3D canvas: the accessible way to open a run.
export function CometList({ comets = [], overflow = 0, onOpen, onMore, onHighlight }) {
  if (!comets.length && !overflow) return null
  return (
    <ul className="co-comet-list" aria-label="Live workflows">
      {comets.map((c) => {
        const head = c.stars[c.headIndex]
        return (
          <li key={c.id}>
            <button
              type="button"
              className="co-comet-item"
              onClick={() => onOpen?.(c)}
              onMouseEnter={onHighlight ? () => onHighlight(c.id) : undefined}
              onMouseLeave={onHighlight ? () => onHighlight(null) : undefined}
              onFocus={onHighlight ? () => onHighlight(c.id) : undefined}
              onBlur={onHighlight ? () => onHighlight(null) : undefined}
              data-testid="comet-item"
            >
              <span className={`co-comet-dot co-comet-dot-${head.state}`} aria-hidden="true" />
              <span className="co-comet-item-title">{c.title}</span>
              <span className="co-comet-item-stage">
                {c.kind === 'thread' ? 'thread · ' : ''}
                {head.name} · {stateLabel(head.state)}
              </span>
            </button>
          </li>
        )
      })}
      {overflow > 0 && (
        <li>
          {onMore ? (
            <button type="button" className="co-comet-item co-comet-more" onClick={onMore} data-testid="comet-more">
              +{overflow} more
            </button>
          ) : (
            <span className="co-comet-more" data-testid="comet-more">
              +{overflow} more
            </span>
          )}
        </li>
      )}
    </ul>
  )
}
