import { Tag, Typography } from 'antd'
import { BRIEF_LABELS } from './brief'

const Line = ({ label, children }) => (
  <div style={{ marginBottom: 4 }}>
    <Typography.Text type="secondary" style={{ fontSize: 12 }}>{label}: </Typography.Text>
    {children}
  </div>
)
const Tags = ({ items }) =>
  items?.length ? items.map((s) => <Tag key={s} style={{ fontFamily: 'monospace' }}>{s}</Tag>) : <Typography.Text type="secondary">none</Typography.Text>

// A task's brief as labelled lines, plus each enclosing bound. Plain text only.
export default function BriefPanel({ brief, bounds = [] }) {
  if (!brief && !bounds?.length) return null
  const b = brief || {}
  const texts = ['goal', 'done', 'stop', 'setup', 'log', 'tie_break'].filter((k) => b[k])
  return (
    <div data-testid="brief-panel" style={{ marginBottom: 16 }}>
      {brief && (
        <>
          {texts.slice(0, 1).map((k) => (
            <Line key={k} label={BRIEF_LABELS[k]}><span style={{ whiteSpace: 'pre-wrap' }}>{b[k]}</span></Line>
          ))}
          <Line label="CAN change"><Tags items={b.can_change} /></Line>
          <Line label="CANNOT change"><Tags items={b.cannot_change} /></Line>
          {texts.slice(1).map((k) => (
            <Line key={k} label={BRIEF_LABELS[k]}><span style={{ whiteSpace: 'pre-wrap' }}>{b[k]}</span></Line>
          ))}
          {b.retries != null && <Line label="Retries">{b.retries}</Line>}
          {b.outputs?.length ? <Line label="Outputs"><Tags items={b.outputs} /></Line> : null}
        </>
      )}
      {(bounds || []).map((x, i) => (
        <div key={`${x.task_id}-${i}`} data-testid="brief-bound" style={{ marginBottom: 4 }}>
          <Typography.Text type="secondary">
            Also bound by an enclosing brief {x.task_id}
          </Typography.Text>{' '}
          CAN: <Tags items={x.can_change} /> CANNOT: <Tags items={x.cannot_change} />
        </div>
      ))}
    </div>
  )
}
