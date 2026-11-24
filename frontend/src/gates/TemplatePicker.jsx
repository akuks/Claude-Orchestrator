import { useEffect, useState } from 'react'
import { Alert, Button, Empty, Modal, Space } from 'antd'
import { api } from '../api'
import FlowPreview from '../flow/FlowPreview'
import MiniFlow from '../flow/MiniFlow'
import './workflows.css'

export const GATE_HINT = "Set your project's own test command in the check step."
// The server's wording of the same hint, shown in the plain wording instead.
const SERVER_GATE_HINT = "Set the project's own test command in the gate."

// The hint to show for a template: the server's own, in plain words, or
// GATE_HINT when the server sends none and the template has a check step.
export function templateHint(t) {
  const hasGate = (t?.stages || []).some((s) => s.kind === 'gate')
  const h = t?.hint
  if (!h) return hasGate ? GATE_HINT : null
  return h === SERVER_GATE_HINT ? GATE_HINT : h
}

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

function TemplateCard({ t, onPick, onOpen }) {
  return (
    <div className="co-wf-tpl" data-testid="template-item">
      <div className="co-wf-tpl-head">
        <div className="co-wf-tpl-name">{t.name}</div>
        <Button size="small" type="primary" onClick={() => onPick(t)} aria-label={`Use this: ${t.name}`}>
          Use this
        </Button>
      </div>
      {t.description && <div className="co-wf-tpl-desc">{t.description}</div>}
      <MiniFlow steps={t.stages || []} label={`See the steps of ${t.name}`} onOpen={() => onOpen(t)} />
    </div>
  )
}

// Templates in two groups: the built-in ones from the server, and the
// team's own (`presets`). Choosing one opens the editor pre-filled.
export default function TemplatePicker({ open, onClose, onPick, presets = [] }) {
  const [templates, setTemplates] = useState([])
  const [error, setError] = useState('')
  const [preview, setPreview] = useState(null)

  useEffect(() => {
    if (!open) return
    setError('')
    api.workflowTemplates().then((t) => setTemplates(t || [])).catch((e) => setError(e.message))
  }, [open])

  return (
    <Modal title="Start from a template" open={open} onCancel={onClose} footer={null} width="min(1040px, 94vw)">
      <p className="co-wf-muted">Pick one to start with. You can change everything before you save it.</p>
      {error && <Alert type="error" showIcon message={error} style={{ marginBottom: 10 }} />}
      <h3 className="co-wf-group">Built-in</h3>
      {templates.length === 0 && !error ? (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="No built-in templates" />
      ) : (
        <div className="co-wf-tpl-grid">
          {templates.map((t) => (
            <TemplateCard key={t.key || t.name} t={t} onPick={onPick} onOpen={setPreview} />
          ))}
        </div>
      )}
      {presets.length > 0 && (
        <>
          <h3 className="co-wf-group">Your team's</h3>
          <div className="co-wf-tpl-grid" data-testid="team-templates">
            {presets.map((t) => (
              <TemplateCard key={t.name} t={t} onPick={onPick} onOpen={setPreview} />
            ))}
          </div>
        </>
      )}
      <FlowPreview
        open={!!preview}
        title={preview?.name}
        description={preview?.description}
        steps={preview?.stages}
        onClose={() => setPreview(null)}
        footer={
          <Space>
            <Button onClick={() => setPreview(null)}>Back</Button>
            <Button
              type="primary"
              onClick={() => {
                const t = preview
                setPreview(null)
                onPick(t)
              }}
              data-testid="preview-use"
            >
              Use this
            </Button>
          </Space>
        }
      />
    </Modal>
  )
}
