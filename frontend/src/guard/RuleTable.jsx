import { useCallback, useEffect, useState } from 'react'
import { Alert, Button, Empty, Form, Input, Modal, Popconfirm, Radio, Space, Switch, Table, Tag, Typography, message } from 'antd'
import { DeleteOutlined, EditOutlined, PlusOutlined } from '@ant-design/icons'
import { api } from '../api'
import { RULES_HELP, SEATBELT_HELP } from './guard'
import './guard.css'

export const PATTERN_MAX = 500
export const REASON_MAX = 200

const KIND_TAG = {
  confirm: { color: 'orange', text: 'Confirm' },
  never: { color: 'red', text: 'Never' },
}

// Client-side checks that mirror the server's (the server stays the judge and
// its message is shown as it is).
export function checkRule({ pattern, match }) {
  const p = String(pattern ?? '')
  if (!p.trim()) return 'a pattern is required'
  if (p.length > PATTERN_MAX) return `a pattern is at most ${PATTERN_MAX} characters`
  if (/[\x00-\x1f\x7f-\x9f]/.test(p)) return 'patterns are one line of text (no tab or newline)'
  if (match !== 'text' && match !== 'regex') return 'match must be text or regex'
  return null
}

function RuleModal({ open, rule, draft, scope, projectId, onClose, onSaved }) {
  const [form] = Form.useForm()
  const [error, setError] = useState(null)
  const [saving, setSaving] = useState(false)
  const isEdit = Boolean(rule)

  useEffect(() => {
    if (open) setError(draft && !draft.pattern ? 'this decision names no plain path; enter the pattern to match' : null)
  }, [open, rule, draft])
  const initial = rule
    ? { kind: rule.kind, match: rule.match, pattern: rule.pattern, reason: rule.reason, enabled: rule.enabled }
    : draft
      ? { kind: draft.kind, match: draft.match === 'regex' ? 'regex' : 'text', pattern: draft.pattern, reason: (draft.reason || '').slice(0, REASON_MAX), name: '', enabled: true }
      : { kind: 'confirm', match: 'text', pattern: '', reason: '', name: '', enabled: true }

  const submit = async () => {
    const v = form.getFieldsValue(true)
    const local = checkRule(v)
    if (local) {
      setError(local)
      return
    }
    setSaving(true)
    setError(null)
    try {
      const body = { kind: v.kind, match: v.match, pattern: v.pattern, reason: v.reason || '', enabled: v.enabled !== false }
      if (isEdit) await api.updateGuardRule(rule.id, body)
      else
        await api.createGuardRule({
          ...body,
          scope,
          ...(scope === 'project' ? { project_id: projectId } : {}),
          ...(v.name?.trim() ? { name: v.name.trim() } : {}),
        })
      message.success(isEdit ? 'Rule saved' : 'Rule added')
      onSaved?.()
      onClose()
    } catch (e) {
      setError(e.message) // the server's validation message, e.g. "invalid regex: ..."
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal
      open={open}
      title={isEdit ? `Edit ${rule.rule_id}` : scope === 'org' ? 'Add an org rule' : 'Add a project rule'}
      onCancel={onClose}
      destroyOnHidden
      footer={[
        <Button key="c" onClick={onClose}>
          Cancel
        </Button>,
        <Button key="s" type="primary" loading={saving} onClick={submit} data-testid="rule-save">
          {isEdit ? 'Save' : 'Add rule'}
        </Button>,
      ]}
    >
      <Form form={form} layout="vertical" initialValues={initial} preserve={false} data-testid="rule-form">
        {!isEdit && (
          <Form.Item name="name" label="Name (optional; shapes the rule id)">
            <Input maxLength={200} placeholder="e.g. no prod deploys" data-testid="rule-name" />
          </Form.Item>
        )}
        <Space size="large" wrap>
          <Form.Item name="kind" label="Kind">
            <Radio.Group
              optionType="button"
              options={[
                { value: 'confirm', label: 'Confirm (needs approval)' },
                { value: 'never', label: 'Never (blocked)' },
              ]}
            />
          </Form.Item>
          <Form.Item name="match" label="Match">
            <Radio.Group
              optionType="button"
              options={[
                { value: 'text', label: 'Text' },
                { value: 'regex', label: 'Regex' },
              ]}
            />
          </Form.Item>
        </Space>
        <Form.Item name="pattern" label="Pattern" extra="Text: a case-insensitive piece of text. Regex: Python syntax, case-insensitive.">
          <Input maxLength={PATTERN_MAX + 100} className="co-guard-mono" placeholder="e.g. kubectl delete" data-testid="rule-pattern" />
        </Form.Item>
        <Form.Item name="reason" label="Reason (the agent and the approver read it)">
          <Input maxLength={REASON_MAX} showCount placeholder="e.g. deleting cluster resources needs a person" data-testid="rule-reason" />
        </Form.Item>
        <Form.Item name="enabled" label="Enabled" valuePropName="checked">
          <Switch />
        </Form.Item>
      </Form>
      {error && <Alert type="error" showIcon message={error} data-testid="rule-error" />}
    </Modal>
  )
}

// Custom co-guard rules of one scope: the org's (projectId unset) or a
// project's. canEdit: add / edit / delete / enable. Everything is plain text.
// draft: a pre-filled new rule to open in the editor (never saved by itself); onDraftUsed clears it.
export default function RuleTable({ scope = 'org', projectId, canEdit = false, title, help = true, draft, onDraftUsed }) {
  const [rules, setRules] = useState([])
  const [loading, setLoading] = useState(false)
  const [loadError, setLoadError] = useState(null)
  const [modal, setModal] = useState(null) // {rule} | {rule: null}
  const [busy, setBusy] = useState(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const list = await api.listGuardRules(scope === 'project' ? projectId : undefined)
      setRules(Array.isArray(list) ? list : [])
      setLoadError(null)
    } catch (e) {
      setLoadError(e.message)
    } finally {
      setLoading(false)
    }
  }, [scope, projectId])

  useEffect(() => {
    load()
  }, [load])

  useEffect(() => {
    if (draft && canEdit) {
      setModal({ rule: null, draft })
      onDraftUsed?.()
    }
  }, [draft, canEdit]) // eslint-disable-line react-hooks/exhaustive-deps

  const toggle = async (rule, enabled) => {
    setBusy(rule.id)
    try {
      await api.updateGuardRule(rule.id, { enabled })
      await load()
    } catch (e) {
      message.error(e.message)
    } finally {
      setBusy(null)
    }
  }
  const remove = async (rule) => {
    setBusy(rule.id)
    try {
      await api.deleteGuardRule(rule.id)
      message.success('Rule deleted')
      await load()
    } catch (e) {
      message.error(e.message)
    } finally {
      setBusy(null)
    }
  }

  const columns = [
    {
      title: 'Rule',
      dataIndex: 'rule_id',
      render: (id, r) => (
        <div className="co-guard-rule-cell">
          <span className="co-guard-mono">{id}</span>
          {r.reason ? <Typography.Text type="secondary">{r.reason}</Typography.Text> : null}
        </div>
      ),
    },
    {
      title: 'Kind',
      dataIndex: 'kind',
      width: 96,
      render: (k) => (
        <Tag color={KIND_TAG[k]?.color} bordered={false}>
          {KIND_TAG[k]?.text || k}
        </Tag>
      ),
    },
    {
      title: 'Pattern',
      dataIndex: 'pattern',
      render: (p, r) => (
        <span className="co-guard-pattern">
          <Tag bordered={false}>{r.match}</Tag>
          <code className="co-guard-mono">{p}</code>
        </span>
      ),
    },
    {
      title: 'On',
      dataIndex: 'enabled',
      width: 64,
      render: (on, r) => (
        <Switch
          size="small"
          checked={on}
          disabled={!canEdit || busy === r.id}
          onChange={(v) => toggle(r, v)}
          aria-label={`Rule ${r.rule_id} enabled`}
          data-testid={`rule-enabled-${r.id}`}
        />
      ),
    },
  ]
  if (canEdit)
    columns.push({
      title: '',
      key: 'actions',
      width: 88,
      render: (_, r) => (
        <Space size={2}>
          <Button size="small" type="text" icon={<EditOutlined />} aria-label={`Edit ${r.rule_id}`} onClick={() => setModal({ rule: r })} />
          <Popconfirm title={`Delete ${r.rule_id}?`} okText="Delete" okButtonProps={{ danger: true }} onConfirm={() => remove(r)}>
            <Button size="small" type="text" danger icon={<DeleteOutlined />} aria-label={`Delete ${r.rule_id}`} disabled={busy === r.id} />
          </Popconfirm>
        </Space>
      ),
    })

  return (
    <div className="co-guard-rule-table" data-testid={`rule-table-${scope}`}>
      <div className="co-guard-rule-head">
        {title && <Typography.Text strong>{title}</Typography.Text>}
        {canEdit && (
          <Button size="small" icon={<PlusOutlined />} onClick={() => setModal({ rule: null })} data-testid={`rule-add-${scope}`}>
            Add rule
          </Button>
        )}
      </div>
      {help && (
        <>
          <Typography.Paragraph type="secondary" className="co-guard-help">
            {RULES_HELP}
          </Typography.Paragraph>
          <Alert className="co-guard-help-alert" type="info" showIcon message={SEATBELT_HELP} data-testid="rule-seatbelt" />
        </>
      )}
      {loadError && <Alert type="error" showIcon message={loadError} style={{ marginBottom: 8 }} />}
      <Table
        rowKey="id"
        size="small"
        loading={loading}
        pagination={false}
        dataSource={rules}
        columns={columns}
        scroll={{ x: true }}
        locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="No rules yet" /> }}
      />
      <RuleModal
        open={Boolean(modal)}
        rule={modal?.rule || null}
        draft={modal?.draft || null}
        scope={scope}
        projectId={projectId}
        onClose={() => setModal(null)}
        onSaved={load}
      />
    </div>
  )
}
