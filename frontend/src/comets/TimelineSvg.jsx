import { useId } from 'react'
import { palette } from '../theme/tokens'
import { Star, smoothPath, stateLabel } from './CometSvg'
import { timelineComet } from './timeline'
import './comets.css'

// Static run-page timeline: the reduced-motion / no-WebGL fallback for the 3D
// CometTimeline. Stage names, states and counts are HTML buttons under their
// stars (keyboard reachable); the SVG above them is decoration.

const W = 640
const H = 120
const PAD_X = 24
const PAD_Y = 18

export function stageCaption(star) {
  const n = star.taskCount
  return `${stateLabel(star.state)}${n ? ` · ${n} task${n === 1 ? '' : 's'}` : ''}`
}

// One stage label/button; shared with the 3D timeline (which positions it).
export function TimelineStageButton({ star, index, selected, hovered, onSelect, style, buttonRef }) {
  return (
    <button
      type="button"
      ref={buttonRef}
      className={`co-tl-stage co-tl-stage-${star.state}${selected ? ' co-tl-stage-selected' : ''}${hovered ? ' co-tl-stage-hover' : ''}`}
      style={style}
      onClick={onSelect ? () => onSelect(index) : undefined}
      aria-pressed={onSelect ? Boolean(selected) : undefined}
      data-testid="timeline-stage"
      title={`${star.name}: ${stageCaption(star)}`}
    >
      <span className="co-tl-stage-name">{star.name}</span>
      <span className="co-tl-stage-state">{stageCaption(star)}</span>
    </button>
  )
}

export default function TimelineSvg({ stages, onSelectStage, selectedIndex }) {
  const uid = useId().replace(/[^a-zA-Z0-9_-]/g, '')
  const { stars, headIndex } = timelineComet(stages)
  if (!stars.length) return null
  const px = (x) => PAD_X + x * (W - 2 * PAD_X)
  const py = (y) => PAD_Y + y * (H - 2 * PAD_Y)
  const pts = stars.map((s) => [px(s.x), py(s.y)])
  const trail = pts.length > 1 ? pts : [[pts[0][0] - 80, pts[0][1] + 8], pts[0]]
  const d = smoothPath(trail)
  const head = (pts[headIndex][0] - trail[0][0]) / Math.max(1, trail[trail.length - 1][0] - trail[0][0])
  const select = onSelectStage ? (i) => onSelectStage(stages[i], i) : undefined
  const width = `${Math.max(12, Math.min(28, 92 / stars.length))}%`

  return (
    <div className="co-comets co-tl" data-testid="comet-timeline-svg">
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" preserveAspectRatio="xMidYMid meet" aria-hidden="true" className="co-comets-svg">
        <defs>
          <linearGradient id={`${uid}-tl`} gradientUnits="userSpaceOnUse" x1={trail[0][0]} y1="0" x2={trail[trail.length - 1][0]} y2="0">
            <stop offset="0" stopColor={palette.amber} />
            <stop offset={Math.max(0, Math.min(1, head))} stopColor={palette.orange} />
            <stop offset={Math.min(1, head + 0.06)} stopColor={palette.rose} stopOpacity=".55" />
            <stop offset="1" stopColor={palette.rose} stopOpacity=".12" />
          </linearGradient>
        </defs>
        <path d={d} className="co-comet-trail" stroke={`url(#${uid}-tl)`} strokeWidth={4} />
        <path d={d} className="co-comet-flow" />
        {stars.map((s, j) => (
          <Star key={`${s.name}-${j}`} star={s} cx={pts[j][0]} cy={pts[j][1]} isHead={j === headIndex} />
        ))}
      </svg>
      <div className="co-tl-stages">
        {stars.map((s, j) => (
          <TimelineStageButton
            key={`${s.name}-${j}`}
            star={s}
            index={j}
            selected={selectedIndex === j}
            onSelect={select}
            style={{ left: `${(px(s.x) / W) * 100}%`, maxWidth: width }}
          />
        ))}
      </div>
    </div>
  )
}
