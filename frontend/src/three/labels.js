// HTML labels over the canvas: written once per frame from projected 3D
// positions. Only `transform` is touched (no layout reads, so no thrash), and
// only when the anchor moved by at least a quarter pixel; the first write also
// reveals the label (they start hidden so nothing flashes at 0,0) and marks
// it data-placed, which ends any static CSS fallback position.
const OFFSET = {
  right: 'translate(10px, calc(-100% - 13px))',
  left: 'translate(calc(-100% - 10px), calc(-100% - 13px))',
  start: 'translate(calc(-100% - 14px), -50%)',
  below: 'translate(-50%, 13px)',
  under: 'translate(-50%, 24px)',
}

export function placeLabel(el, p, side = 'right') {
  const x = Math.round(p.x * 4) / 4
  const y = Math.round(p.y * 4) / 4
  const last = el._pos
  if (last && Math.abs(last.x - x) < 0.25 && Math.abs(last.y - y) < 0.25) return
  el._pos = { x, y }
  el.style.transform = `translate3d(${x}px, ${y}px, 0) ${OFFSET[side] || OFFSET.right}`
  if (!last) {
    el.style.visibility = 'visible'
    el.setAttribute('data-placed', '') // ends any CSS fallback position
  }
}

// Label centres for a column of labels: each wants to sit at its anchor y,
// but none may overlap (gap px apart) and the stack stays inside [0, height].
// Returns centres in input order.
export function stackLabels(items, height, gap = 4) {
  const order = items.map((it, i) => i).sort((a, b) => items[a].y - items[b].y || a - b)
  const top = new Array(items.length)
  let floor = -Infinity
  for (const i of order) {
    const { y, h } = items[i]
    top[i] = Math.max(y - h / 2, floor, 0)
    floor = top[i] + h + gap
  }
  // over the bottom edge: walk back up, pulling each label above the next
  let ceil = height
  for (let k = order.length - 1; k >= 0; k--) {
    const i = order[k]
    const h = items[i].h
    if (top[i] + h > ceil) top[i] = ceil - h
    ceil = top[i] - gap
  }
  return items.map((it, i) => top[i] + it.h / 2)
}
