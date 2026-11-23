import { Alert, Button, Space } from 'antd'
import { nudgeText } from './launch'

// Shown inside the new-task modal when the prompt looks like new scope. It never blocks: "Run anyway"
// creates the task as it was, and closing the modal does nothing.
export default function ScopeNudge({ open, wouldBe, busy, onAdd, onAfter, onRunAnyway }) {
  return (
    <Alert
      type="info"
      showIcon
      data-testid="scope-nudge"
      style={{ marginBottom: 12 }}
      message={<span data-testid="scope-nudge-text">{nudgeText(open, wouldBe)}</span>}
      description={
        <Space wrap>
          <Button size="small" type="primary" disabled={busy} data-testid="nudge-add" onClick={onAdd}>Add to launch list</Button>
          <Button size="small" disabled={busy} data-testid="nudge-after" onClick={onAfter}>After launch</Button>
          <Button size="small" type="text" disabled={busy} data-testid="nudge-run" onClick={onRunAnyway}>Run anyway</Button>
        </Space>
      }
    />
  )
}
