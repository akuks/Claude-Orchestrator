import { useEffect, useState } from 'react'
import { Alert, Button, Empty, List, Modal, Typography } from 'antd'
import { api } from '../api'

export const GATE_HINT = "Set the project's own test command in the gate."

// Agent names a template uses that the install does not have yet.
export function missingAgents(stages, agents) {
  const have = new Set((agents || []).map((a) => a.name))
  const out = []
  for (const st of stages || []) {
    for (const it of st.items || []) {
      if (it.agent && !have.has(it.agent) && !out.includes(it.agent)) out.push(it.agent)
    }
  }
  return out
}

export default function TemplatePicker({ open, onClose, onPick }) {
  const [templates, setTemplates] = useState([])
  const [error, setError] = useState('')

  useEffect(() => {
    if (!open) return
    setError('')
    api.workflowTemplates().then((t) => setTemplates(t || [])).catch((e) => setError(e.message))
  }, [open])

  return (
    <Modal title="New from template" open={open} onCancel={onClose} footer={null}>
      {error && <Alert type="error" showIcon message={error} style={{ marginBottom: 10 }} />}
      {templates.length === 0 && !error && <Empty description="No templates" />}
      <List
        dataSource={templates}
        renderItem={(t) => (
          <List.Item
            data-testid="template-item"
            actions={[<Button key="use" size="small" type="primary" onClick={() => onPick(t)}>Use</Button>]}
          >
            <List.Item.Meta
              title={t.name}
              description={<Typography.Text type="secondary">{t.description}</Typography.Text>}
            />
          </List.Item>
        )}
      />
    </Modal>
  )
}
