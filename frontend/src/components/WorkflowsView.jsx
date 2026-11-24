import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Alert, Button, Drawer, Dropdown, Empty, Form, Input, InputNumber, Modal, Space, Tag, message } from 'antd'
import {
  AppstoreOutlined,
  ArrowUpOutlined,
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
  SettingOutlined,
} from '@ant-design/icons'
import { api } from '../api'
import { useRoute } from '../shell/router'
import StageFields from '../gates/StageFields'
import AgentItems from '../gates/AgentItems'
import StepFlow from '../gates/StepFlow'
import TemplatePicker, { missingAgents, templateHint } from '../gates/TemplatePicker'
import ImportWorkflow from '../gates/ImportWorkflow'
import { RUN_STATUS_COLOR, budgetPayload, exportFileName, formStage, runStatusText, runStepText, stageKind, stagePayload } from '../gates/gates'
import FlowCanvas from '../flow/FlowCanvas'
import { errorBoxes, fieldSelection, newStep, selAfterMove, selAfterRemove } from '../flow/flow'
import '../gates/workflows.css'

export const NEW_MENU = [
  { key: 'template', label: 'Start from a template', icon: <AppstoreOutlined /> },
  { key: 'blank', label: 'Start blank', icon: <FileAddOutlined /> },
  { key: 'import', label: 'Import from a file', icon: <ImportOutlined /> },
]

// ---- Editor ----------------------------------------------------------------
const SEL_WORD = { agent: 'Agent', gate: 'Check', approval: 'Ask a person' }

// What the settings panel is editing, in a few words: "Step 2 · Check".
function selectionTitle(sel, stages) {
  if (!sel) return ''
  const st = (stages || [])[sel.step]
  const kind = stageKind(st)
  const n = st?.items?.length || 0
  const what =
    sel.item === 'fix'
      ? 'Fixer'
      : kind === 'agent'
        ? n > 1
          ? `Agent ${(sel.item ?? 0) + 1} of ${n}`
          : 'Agent'
        : SEL_WORD[kind]
  return `Step ${sel.step + 1} · ${what}`
}

// The selection, kept inside the steps that exist (an agent removed in the
// panel moves it to the step's last agent).
function clampSel(sel, stages) {
  if (!sel) return null
  const list = stages || []
  if (sel.step >= list.length) return null
  if (typeof sel.item === 'number') {
    const n = list[sel.step]?.items?.length || 0
    if (stageKind(list[sel.step]) !== 'agent') return { step: sel.step, item: null }
    if (n && sel.item >= n) return { step: sel.step, item: n - 1 }
  }
  return sel
}

const sameKeys = (a, b) => a.size === b.size && [...a].every((k) => b.has(k))

function WorkflowEditor({ open, workflow, prefill, hint, missing = [], agents, onClose, onSaved }) {
  const [form] = Form.useForm()
  const [saving, setSaving] = useState(false)
  const [projects, setProjects] = useState([])
  const [error, setError] = useState('')
  const [rawSel, setSel] = useState(null)
  const [errKeys, setErrKeys] = useState(() => new Set())
  const [settingsOpen, setSettingsOpen] = useState(false)
  const listOps = useRef(null)
  const watched = Form.useWatch('stages', { form, preserve: true })
  const stages = useMemo(() => (Array.isArray(watched) ? watched : []), [watched])
  const sel = clampSel(rawSel, stages)
  const isEdit = !!workflow
  const blankStart = open && !workflow && !prefill

  useEffect(() => {
    if (!open) return
    setError('')
    setSel(null)
    setErrKeys(new Set())
    setSettingsOpen(workflow?.budget_usd != null)
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
      form.setFieldsValue({ stages: [] })
    }
  }, [open, workflow, prefill])

  useEffect(() => {
    if (!open) return
    api.listProjects?.().then((p) => setProjects(p || [])).catch(() => {})
  }, [open])

  // Red dots follow the form's live errors.
  const fieldsChanged = () => {
    const next = errorBoxes(form.getFieldsError(), form.getFieldValue('stages'))
    setErrKeys((cur) => (sameKeys(cur, next) ? cur : next))
  }

  const submit = async () => {
    let v
    try {
      v = await form.validateFields()
    } catch (e) {
      const errs = (e?.errorFields || []).filter((f) => f.errors?.length)
      const cur = form.getFieldValue('stages')
      setErrKeys(errorBoxes(errs, cur))
      const first = errs.map((f) => fieldSelection(f.name, cur)).find(Boolean)
      if (first) setSel(first)
      return
    }
    if (!(v.stages || []).length) {
      setError('Add at least one step before you save.')
      return
    }
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

  const insert = (index, kind) => {
    listOps.current?.add(newStep(kind), index)
    setErrKeys(new Set())
    setError('')
    setSel({ step: index, item: kind === 'agent' ? 0 : null })
  }
  const move = (index, dir) => {
    const to = index + dir
    if (to < 0 || to >= stages.length) return
    listOps.current?.move(index, to)
    setErrKeys(new Set())
    setSel((cur) => selAfterMove(cur, index, to))
  }
  const removeAt = (index) => {
    listOps.current?.remove(index)
    setErrKeys(new Set())
    setSel((cur) => selAfterRemove(cur, index))
  }
  const addAgentAt = (index) => {
    const items = form.getFieldValue(['stages', index, 'items']) || []
    form.setFieldValue(['stages', index, 'items'], [...items, {}])
    setSel({ step: index, item: items.length })
  }

  const agentOptions = agents.map((a) => ({ value: a.name, label: a.name }))
  const hasFix = stages.some((st) => stageKind(st) === 'gate' && st?.fix?.agent)

  return (
    <Drawer
      title={isEdit ? `Edit “${workflow.name}”` : 'New workflow'}
      open={open}
      onClose={onClose}
      width="min(1200px, 96vw)"
      forceRender
      className="co-wf-ed"
      extra={
        <Space>
          <Button onClick={onClose}>Cancel</Button>
          <Button type="primary" loading={saving} onClick={submit}>
            Save
          </Button>
        </Space>
      }
    >
      <Form form={form} layout="vertical" requiredMark={false} onFieldsChange={fieldsChanged}>
        <div className="co-wf-ed-head">
          <Form.Item name="name" label="Name" rules={[{ required: true, message: 'Give the workflow a name' }]} className="co-wf-ed-name">
            <Input placeholder="e.g. Build and test a feature" />
          </Form.Item>
          <Form.Item name="description" label="What is it for? (optional)" className="co-wf-ed-desc">
            <Input placeholder="One sentence your team sees on the card" />
          </Form.Item>
          <Button
            className="co-wf-ed-settings-btn"
            icon={<SettingOutlined />}
            onClick={() => setSettingsOpen((o) => !o)}
            aria-expanded={settingsOpen}
            data-testid="workflow-settings"
          >
            Settings
          </Button>
        </div>
        <div className="co-wf-ed-settings" hidden={!settingsOpen} data-testid="workflow-settings-panel">
          <Form.Item
            name="budget_usd"
            label="Spending limit for each run (optional)"
            extra="When a run reaches this amount it pauses before starting more work. Nothing already running is stopped."
            style={{ marginBottom: 0 }}
          >
            <InputNumber
              prefix="$"
              min={0.01}
              max={10000}
              step={1}
              precision={2}
              placeholder="No limit"
              style={{ width: '100%', maxWidth: 260 }}
              aria-label="Spending limit for each run"
              data-testid="workflow-budget"
            />
          </Form.Item>
        </div>
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
        <Form.List name="stages">
          {(fields, ops) => {
            listOps.current = ops
            return (
              <>
                <div className={`co-flow-frame co-wf-ed-canvas${hasFix ? ' co-wf-ed-canvas-tall' : ''}`}>
                  <FlowCanvas
                    mode="edit"
                    steps={stages}
                    agents={agents}
                    selected={sel}
                    onSelect={setSel}
                    onInsert={insert}
                    onMove={move}
                    onRemove={removeAt}
                    onAddAgent={addAgentAt}
                    errors={errKeys}
                    focusStep={sel?.step ?? null}
                    pulseAdd={blankStart}
                  />
                </div>
                <div className="co-wf-ed-panel" data-testid="step-panel">
                  {!sel && (
                    <div className="co-wf-ed-hint" data-testid="step-panel-hint">
                      <ArrowUpOutlined aria-hidden="true" /> Click a box to edit it, or + to add a step.
                    </div>
                  )}
                  {sel && (
                    <div className="co-wf-ed-panel-title" data-testid="step-panel-title">
                      {selectionTitle(sel, stages)}
                    </div>
                  )}
                  {fields.map((f, si) => {
                    const on = sel?.step === si
                    const focus = on && sel.item === 'fix' ? 'fix' : undefined
                    return (
                      <section key={f.key} hidden={!on} aria-label={`Step ${si + 1}`} data-testid="step-card" className="co-wf-ed-step">
                        <div hidden={focus === 'fix'}>
                          <Form.Item name={[f.name, 'name']} label="Step name" rules={[{ required: true, message: 'Give the step a name' }]}>
                            <Input placeholder="e.g. Build it" aria-label={`Name of step ${si + 1}`} />
                          </Form.Item>
                        </div>
                        <StageFields
                          name={f.name}
                          projects={projects}
                          agents={agents}
                          showKind={false}
                          focus={focus}
                          agentFields={
                            <AgentItems
                              stageName={f.name}
                              stepIndex={si}
                              agentOptions={agentOptions}
                              only={on && typeof sel.item === 'number' ? sel.item : null}
                              showAdd={false}
                            />
                          }
                        />
                      </section>
                    )
                  })}
                </div>
              </>
            )
          }}
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
