import dayjs from 'dayjs'
import { parseTime } from '../home/useHomeData'

// The run's log, newest first. Text only.
export default function RunLog({ log }) {
  const lines = [...(Array.isArray(log) ? log : [])].reverse()
  if (!lines.length) return null
  return (
    <details className="co-surface co-run-panel co-run-log" data-testid="run-log">
      <summary>Run log ({lines.length})</summary>
      <ul>
        {lines.map((l, i) => (
          <li key={i}>
            <span className="co-num">{l.at ? dayjs(parseTime(l.at)).format('HH:mm:ss') : ''}</span> {l.text}
          </li>
        ))}
      </ul>
    </details>
  )
}
