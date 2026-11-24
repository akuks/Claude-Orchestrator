import { Modal } from 'antd'
import FlowCanvas from './FlowCanvas'

// A workflow or template, read-only, as the full-size canvas. `footer` holds
// the actions (Edit, Run, Use this).
export default function FlowPreview({ open, title, description, steps, agents, footer, onClose }) {
  return (
    <Modal open={open} title={title} onCancel={onClose} footer={footer} width="min(1320px, 96vw)" destroyOnClose>
      {description && <p className="co-wf-muted">{description}</p>}
      <div className="co-flow-frame" data-testid="flow-preview">
        <FlowCanvas center mode="view" steps={steps || []} agents={agents} label={`Steps of ${title || 'this workflow'}`} />
      </div>
    </Modal>
  )
}
