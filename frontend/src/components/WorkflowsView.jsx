import { useCallback, useEffect, useState } from 'react'
import { Alert, Button, Drawer, Dropdown, Empty, Form, Input, InputNumber, Modal, Space, Tag, message } from 'antd'
import {
  AppstoreOutlined,
  DeleteOutlined,
  DownOutlined,
  DownloadOutlined,
  EditOutlined,
  EllipsisOutlined,
  FileAddOutlined,
  ImportOutlined,
  PlayCircleOutlined,
  PlusOutlined,
  ReloadOutlined,
} from '@ant-design/icons'
import { api } from '../api'
import { useRoute } from '../shell/router'
import StageFields from '../gates/StageFields'
import AgentItems from '../gates/AgentItems'
import StepFlow from '../gates/StepFlow'
import TemplatePicker, { missingAgents, templateHint } from '../gates/TemplatePicker'
import ImportWorkflow from '../gates/ImportWorkflow'
import { RUN_STATUS_COLOR, budgetPayload, exportFileName, formStage, runStatusText, runStepText, stagePayload } from '../gates/gates'
import '../gates/workflows.css'

export const NEW_MENU = [
  { key: 'template', label: 'Start from a template', icon: <AppstoreOutlined /> },
  { key: 'blank', label: 'Start blank', icon: <FileAddOutlined /> },
  { key: 'import', label: 'Import from a file', icon: <ImportOutlined /> },
]

// ---- Editor ----------------------------------------------------------------
function WorkflowEditor({ open, workflow, prefill, hint, missing = [], agents, onClose, onSaved }) {
  const [form] = Form.useForm()
  const [saving, setSaving] = useState(false)
  const [projects, setProjects] = useState([])
  const [error, setError] = useState('')
  const isEdit = !!workflow

  useEffect(() => {
    if (!open) return
    setError('')
    if (workflow) {
      form.setFieldsValue({ ...workflow, budget_usd: workflow.budget_usd ?? null, stages: (workflow.stages || []).map(formStage) })
    } else if (prefill) {
      form.resetFields()
      form.setFieldsValue({
        name: prefill.name,
        description: prefill.description,
        stages: (prefill.stages || []).map(formStage),
      })
    } else {
      form.resetFields()
      form.setFieldsValue({ stages: [{ name: 'Build it', kind: 'agent', items: [{}] }] })
    }
  }, [open, workflow, prefill])

  useEffect(() => {
    if (!open) return
    api.listProjects?.().then((p) => setProjects(p || [])).catch(() => {})
  }, [open])

  const submit = async () => {
    const v = await form.validateFields().catch(() => null)
    if (!v) return
    setSaving(true)
    setError('')
    const { budget_usd: budget, ...rest } = v
    const payload = { ...rest, stages: (v.stages || []).map((st) => stagePayload(st, agents)), ...budgetPayload(budget, workflow?.budget_usd) }
    try {
      if (isEdit) await api.updateWorkflow(workflow.id, payload)
      else await api.createWorkflow(payload)
      message.success(isEdit ? 'Workflow saved' : 'Workflow created')
      onSaved?.()
      onClose()
    } catch (e) {
      setError(e.message)
    } finally {
      setSaving(false)
    }
  }

  const agentOptions = agents.map((a) => ({ value: a.name, label: a.name }))

  return (
    <Drawer
      title={isEdit ? `Edit “${workflow.name}”` : 'New workflow'}
      open={open}
      onClose={onClose}
      width="min(1040px, 92vw)"
      forceRender
      extra={
        <Space>
          <Button onClick={onClose}>Cancel</Button>
          <Button type="primary" loading={saving} onClick={submit}>
            Save
          </Button>
        </Space>
      }
    >
      <Form form={form} layout="vertical" requiredMark={false}>
        <Form.Item name="name" label="Name" rules={[{ required: true, message: 'Give the workflow a name' }]}>
          <Input placeholder="e.g. Build and test a feature" />
        </Form.Item>
        <Form.Item name="description" label="What is it for? (optional)">
          <Input placeholder="One sentence your team sees on the card" />
        </Form.Item>
        <Form.Item
          name="budget_usd"
          label="Spending limit for each run (optional)"
          extra="When a run reaches this amount it pauses before starting more work. Nothing already running is stopped."
        >
          <InputNumber
            prefix="$"
            min={0.01}
            max={10000}
            step={1}
            precision={2}
            placeholder="No limit"
            style={{ width: '100%' }}
            aria-label="Spending limit for each run"
            data-testid="workflow-budget"
          />
        </Form.Item>
        {hint && <Alert data-testid="template-hint" type="info" showIcon message={hint} style={{ marginBottom: 10 }} />}
        {missing.length > 0 && (
          <Alert
            data-testid="missing-agents"
            type="warning"
            showIcon
            message={`Add these helpers from Agents → presets first: ${missing.join(', ')}`}
            style={{ marginBottom: 10 }}
          />
        )}
        <div className="co-wf-section" style={{ marginTop: 8 }}>Steps</div>
        <p className="co-wf-muted" data-testid="steps-help">
          Steps run top to bottom. Agents in the same step work at the same time.
        </p>
        <Form.List name="stages">
          {(stages, { add: addStage, remove: removeStage }) => (
            <div className="co-wf-steps">
              {stages.map((stage, si) => (
                <section key={stage.key} className="co-wf-step" aria-label={`Step ${si + 1}`} data-testid="step-card">
                  <div className="co-wf-step-head">
                    <span className="co-wf-step-no" aria-hidden="true">
                      {si + 1}
                    </span>
                    <Form.Item name={[stage.name, 'name']} rules={[{ required: true, message: 'Give the step a name' }]}>
                      <Input className="co-wf-step-name" placeholder="Step name, e.g. Build it" aria-label={`Name of step ${si + 1}`} />
                    </Form.Item>
                    <Button
                      danger
                      type="text"
                      icon={<DeleteOutlined />}
                      onClick={() => removeStage(stage.name)}
                      aria-label={`Remove step ${si + 1}`}
                    />
                  </div>
                  <StageFields
                    name={stage.name}
                    projects={projects}
                    agents={agents}
                    agentFields={<AgentItems stageName={stage.name} stepIndex={si} agentOptions={agentOptions} />}
                  />
                </section>
              ))}
              <Button type="dashed" block icon={<PlusOutlined />} onClick={() => addStage({ name: '', kind: 'agent', items: [{}] })}>
                Add a step
              </Button>
            </div>
          )}
        </Form.List>
        {error && <Alert type="error" showIcon message={error} style={{ marginTop: 12 }} />}
      </Form>
    </Drawer>
  )
}

// ---- Run dialog ------------------------------------------------------------
function RunDialog({ workflow, onClose, onRan }) {
  const [input, setInput] = useState('')
  const [running, setRunning] = useState(false)
  const go = async () => {
    setRunning(true)
    try {
      const run = await api.runWorkflow(workflow.id, { input })
      message.success('Workflow started')
      onRan?.(run.id)
      onClose()
    } catch (e) {
      message.error(e.message)
    } finally {
      setRunning(false)
    }
  }
  return (
    <Modal open title={`Run “${workflow?.name}”`} onCancel={onClose} onOk={go} confirmLoading={running} okText="Start">
      <p className="co-wf-muted">
        What should it work on? For example a pull request link, an issue number, or a short description of the change.
      </p>
      <Input.TextArea
        rows={3}
        value={input}
        onChange={(e) => setInput(e.target.value)}
        aria-label="What to run it on"
        placeholder="e.g. PR 1417, issue 1807, or 'add a dark-mode toggle'"
      />
    </Modal>
  )
}

// ---- One workflow card -----------------------------------------------------
function WorkflowCard({ w, isAdmin, onRun, onEdit, onExport, onDelete }) {
  const items = [
    isAdmin && { key: 'edit', label: 'Edit', icon: <EditOutlined /> },
    { key: 'export', label: 'Export', icon: <DownloadOutlined /> },
    isAdmin && { type: 'divider' },
    isAdmin && { key: 'delete', label: 'Delete', icon: <DeleteOutlined />, danger: true },
  ].filter(Boolean)
  const onMenu = ({ key }) => {
    if (key === 'edit') onEdit(w)
    else if (key === 'export') onExport(w)
    else if (key === 'delete')
      Modal.confirm({
        title: `Delete “${w.name}”?`,
        content: 'This removes the workflow. It cannot be undone.',
        okText: 'Delete',
        okButtonProps: { danger: true },
        onOk: () => onDelete(w.id),
      })
  }
  const n = (w.stages || []).length
  return (
    <article className="co-wf-card" data-testid="workflow-card">
      <div className="co-wf-card-head">
        <div className="co-wf-card-name">{w.name}</div>
        <Dropdown menu={{ items, onClick: onMenu }} trigger={['click']}>
          <Button type="text" size="small" icon={<EllipsisOutlined />} aria-label={`More actions for ${w.name}`} data-testid="workflow-more" />
        </Dropdown>
      </div>
      {w.description && <p className="co-wf-card-desc">{w.description}</p>}
      <StepFlow stages={w.stages || []} />
      <div className="co-wf-card-foot">
        <span>{n === 1 ? '1 step' : `${n} steps`}</span>
        <Button type="primary" icon={<PlayCircleOutlined />} onClick={() => onRun(w)}>
          Run
        </Button>
      </div>
    </article>
  )
}

export default function WorkflowsView({ agents = [], isAdmin = false, runWorkflowId = null, onRunHandled }) {
  const [workflows, setWorkflows] = useState([])
  const [presets, setPresets] = useState([])
  const [runs, setRuns] = useState([])
  const [loading, setLoading] = useState(false)
  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState(null)
  const [prefill, setPrefill] = useState(null)
  const [hint, setHint] = useState(null)
  const [missing, setMissing] = useState([])
  const [pickerOpen, setPickerOpen] = useState(false)
  const [importOpen, setImportOpen] = useState(false)
  const [runWf, setRunWf] = useState(null)
  const [, navigate] = useRoute()
  const [loaded, setLoaded] = useState(false)
  const [loadFailed, setLoadFailed] = useState(false)

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const [w, p, r] = await Promise.all([
        api.listWorkflows(),
        api.workflowPresets(),
        api.listWorkflowRuns(),
      ])
      setWorkflows(w)
      setPresets(p)
      setRuns(r)
      setLoaded(true)
      setLoadFailed(false)
    } catch (e) {
      setLoadFailed(true)
      message.error(e.message)
    } finally {
      setLoading(false)
    }
  }, [])

  // Open the run dialog requested from ⌘K once the workflow list is in.
  useEffect(() => {
    if (!runWorkflowId || !loaded) return
    const wf = workflows.find((w) => String(w.id) === String(runWorkflowId))
    if (wf) setRunWf(wf)
    else message.warning('That workflow is no longer available.')
    onRunHandled?.()
  }, [runWorkflowId, loaded, workflows, onRunHandled])

  // The list never came in: drop the request rather than leave it pending to
  // fire on a later, unrelated visit.
  useEffect(() => {
    if (runWorkflowId && !loaded && loadFailed) onRunHandled?.()
  }, [runWorkflowId, loaded, loadFailed, onRunHandled])

  useEffect(() => {
    refresh()
    const id = setInterval(() => api.listWorkflowRuns().then(setRuns).catch(() => {}), 4000)
    return () => clearInterval(id)
  }, [refresh])

  const openNew = () => {
    setEditing(null); setPrefill(null); setHint(null); setMissing([]); setModalOpen(true)
  }

  const openEdit = (w) => {
    setEditing(w); setPrefill(null); setHint(null); setMissing([]); setModalOpen(true)
  }

  // A built-in template or one of the team's: open the editor pre-filled.
  const fromTemplate = async (t) => {
    let known = agents
    try {
      known = (await api.listAgents?.()) || agents
    } catch (_) {}
    setEditing(null)
    setPrefill({ name: t.name, description: t.description, stages: t.stages })
    setHint(templateHint(t))
    setMissing(missingAgents(t.stages, known))
    setPickerOpen(false)
    setModalOpen(true)
  }

  // The server creates the workflow on import; open it for review and refresh.
  const imported = (wf) => {
    setImportOpen(false)
    setPrefill(null); setHint(null); setMissing([])
    setEditing(wf)
    setModalOpen(true)
    refresh()
  }

  const exportWf = async (w) => {
    try {
      const text = await api.exportWorkflow(w.id)
      const url = URL.createObjectURL(new Blob([text], { type: 'text/markdown' }))
      const a = document.createElement('a')
      a.href = url
      a.download = exportFileName(w.name)
      document.body.appendChild(a)
      a.click()
      a.remove()
      URL.revokeObjectURL(url)
    } catch (e) {
      message.error(e.message)
    }
  }

  const remove = async (id) => {
    try {
      await api.deleteWorkflow(id)
      refresh()
    } catch (e) {
      message.error(e.message)
    }
  }

  const onNewMenu = ({ key }) => {
    if (key === 'template') setPickerOpen(true)
    else if (key === 'blank') openNew()
    else if (key === 'import') setImportOpen(true)
  }

  return (
    <>
      <div className="co-wf-head">
        <p>A workflow is a set of steps your agents follow, with checks and approvals in between.</p>
        <div className="co-wf-head-actions">
          <Button icon={<ReloadOutlined />} onClick={refresh} loading={loading}>
            Refresh
          </Button>
          {isAdmin && (
            <Dropdown menu={{ items: NEW_MENU, onClick: onNewMenu }} trigger={['click']}>
              <Button type="primary" icon={<PlusOutlined />} data-testid="new-workflow">
                New workflow <DownOutlined />
              </Button>
            </Dropdown>
          )}
        </div>
      </div>

      {workflows.length === 0 && loaded && !loading ? (
        <div className="co-wf-empty" data-testid="workflows-empty">
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={
              isAdmin
                ? 'No workflows yet. Start from a ready-made template and change it to fit your team.'
                : 'No workflows yet. Ask an admin to add one.'
            }
          >
            {isAdmin && (
              <Button type="primary" icon={<AppstoreOutlined />} onClick={() => setPickerOpen(true)}>
                Start from a template
              </Button>
            )}
          </Empty>
        </div>
      ) : (
        <div className="co-wf-grid">
          {workflows.map((w) => (
            <WorkflowCard key={w.id} w={w} isAdmin={isAdmin} onRun={setRunWf} onEdit={openEdit} onExport={exportWf} onDelete={remove} />
          ))}
        </div>
      )}

      {runs.length > 0 && (
        <>
          <h3 className="co-wf-section">Recent runs</h3>
          <div className="co-wf-runs">
            {runs.slice(0, 10).map((r) => (
              <a key={r.id} href={`#/runs/${encodeURIComponent(r.id)}`} data-testid="workflow-run-link" className="co-wf-run">
                <Tag color={RUN_STATUS_COLOR[r.status]} className="co-wf-run-tag" data-testid="run-status">
                  {runStatusText(r.status)}
                </Tag>
                <span className="co-wf-run-name">{r.name}</span>
                <span className="co-wf-run-input">{r.input}</span>
                <span className="co-wf-run-step">{runStepText(r)}</span>
              </a>
            ))}
          </div>
        </>
      )}

      <TemplatePicker open={pickerOpen} presets={presets} onClose={() => setPickerOpen(false)} onPick={fromTemplate} />
      <ImportWorkflow open={importOpen} onClose={() => setImportOpen(false)} onImported={imported} />
      <WorkflowEditor open={modalOpen} workflow={editing} prefill={prefill} hint={hint} missing={missing} agents={agents} onClose={() => setModalOpen(false)} onSaved={refresh} />
      {runWf && <RunDialog workflow={runWf} onClose={() => setRunWf(null)} onRan={(id) => navigate({ page: 'runs', id })} />}
    </>
  )
}
