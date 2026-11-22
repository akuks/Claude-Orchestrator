import { Alert } from 'antd'
import { notices } from './resume'
import { revealHidden } from '../guard/guard'
import './resume.css'

const TYPE = {
  recovered: 'info',
  credentials_changed: 'info',
  outside_commits: 'info',
  gave_up: 'error',
  history_rewritten: 'warning',
  no_commits: 'warning',
  temp_files: 'warning',
}

export default function RunNotices({ task, events, handoff }) {
  const list = notices(task, events, handoff)
  if (!list.length) return null
  return (
    <div className="co-resume-notices" data-testid="run-notices">
      {list.map((n) => (
        <Alert
          key={n.kind}
          showIcon
          type={TYPE[n.kind] || 'info'}
          className={`co-resume-notice-${n.kind}`}
          data-testid={`notice-${n.kind}`}
          message={revealHidden(n.text).text}
        />
      ))}
    </div>
  )
}
