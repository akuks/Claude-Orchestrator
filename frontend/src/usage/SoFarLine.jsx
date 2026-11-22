import { soFarLine } from './usage'
import './usage.css'

// The thread's "so far" totals, under the task header.
export default function SoFarLine({ thread }) {
  const line = soFarLine(thread)
  if (!line) return null
  return (
    <div className="co-usage-so-far" data-testid="usage-so-far">
      <span className="co-usage-so-far-label">So far</span> {line}
    </div>
  )
}
