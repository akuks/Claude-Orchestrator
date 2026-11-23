import { useEffect, useState } from 'react'
import { Alert, Button, Form, Input, Modal, Select } from 'antd'
import { api } from '../api'
import { CHANGED_MSG, KINDS, LIMITS, checkDecision, longEntryHint } from './decisions'

// Add (decision unset) or edit one decision. A server refusal is shown as it
// is; a 409 on edit tells the pane to reload.
export default function DecisionForm({ open, projectId, decision, onClose, onSaved, onConflict, proposal = null, onProposalError }) {
  const [form] = Form.useForm()
  const [error, setError] = useState(null)
  const [saving, setSaving] = useState(false)
  const isEdit = Boolean(decision)
  useEffect(() => {
    if (open) setError(null)
  }, [open])
  const initial = proposal
    ? { kind: proposal.kind, title: proposal.title, why: proposal.why || '', see: '', touches: proposal.touches || [], source: '' }
    : decision
    ? { kind: decision.kind, title: decision.title, why: decision.why, see: decision.see || '', touches: decision.touches, source: decision.source }
    : { kind: 'decision', title: '', why: '', see: '', touches: [], source: '' }

  // a reload after a 409 brings a new version: show its fields
  useEffect(() => {
    if (open && decision) form.setFieldsValue(initial)
  }, [decision?.version]) // eslint-disable-line react-hooks/exhaustive-deps

  const watchTitle = Form.useWatch('title', form)
  const watchWhy = Form.useWatch('why', form)
  const hint = longEntryHint(watchTitle, watchWhy)

  const submit = async () => {
    const v = form.getFieldsValue(true)
    const local = checkDecision(v)
    if (local) return setError(local)
    const body = {
      kind: v.kind,
      title: v.title.trim(),
      why: v.why || '',
      see: (v.see || '').trim() || null,
      touches: (v.touches || []).map((t) => String(t).trim()).filter(Boolean),
      source: v.source || '',
    }
    setSaving(true)
    setError(null)
    try {
      if (proposal) await api.acceptProposal(projectId, proposal.id, { kind: body.kind, title: body.title, why: body.why, see: body.see, touches: body.touches })
      else if (isEdit) await api.updateDecision(projectId, decision.number, { ...body, version: decision.version })
      else await api.createDecision(projectId, body)
      onSaved?.()
      onClose()
    } catch (e) {
      setError(e.message)
      if (proposal && e.status === 409) onProposalError?.(e)
      else if (isEdit && e.status === 409) onConflict?.(e.message || CHANGED_MSG)
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal
      open={open}
      title={proposal ? 'Accept suggestion' : isEdit ? `Edit ${decision.display_id}` : 'Add a decision'}
      onCancel={onClose}
      destroyOnHidden
      footer={[
        <Button key="c" onClick={onClose}>Cancel</Button>,
        <Button key="s" type="primary" loading={saving} onClick={submit} data-testid="decision-save">
          {isEdit || proposal ? 'Save' : 'Add'}
        </Button>,
      ]}
    >
      <Form form={form} layout="vertical" initialValues={initial} preserve={false} data-testid="decision-form">
        <Form.Item name="kind" label="Kind">
          <Select options={KINDS.map((k) => ({ value: k, label: k }))} data-testid="decision-kind" />
        </Form.Item>
        <Form.Item name="title" label="Title">
          <Input maxLength={LIMITS.title} data-testid="decision-title" />
        </Form.Item>
        <Form.Item name="why" label="Why">
          <Input.TextArea rows={3} maxLength={LIMITS.why} data-testid="decision-why" />
        </Form.Item>
        {hint && <Alert type="warning" showIcon style={{ marginBottom: 12 }} message={hint} data-testid="decision-long-hint" />}
        <Form.Item name="see" label="See (required for a reference)">
          <Input maxLength={LIMITS.see} placeholder="e.g. docs/payments.md" data-testid="decision-see" />
        </Form.Item>
        <Form.Item name="touches" label="Touches (file paths or globs; agents see this before editing them)">
          <Select mode="tags" tokenSeparators={[',']} open={false} placeholder="e.g. src/billing/**" data-testid="decision-touches" />
        </Form.Item>
        {!proposal && (
          <Form.Item name="source" label="Source">
            <Input maxLength={LIMITS.source} placeholder="e.g. PR #42" data-testid="decision-source" />
          </Form.Item>
        )}
      </Form>
      {error && <Alert type="error" showIcon message={error} data-testid="decision-error" />}
    </Modal>
  )
}
