import { useCallback, useEffect, useState } from 'react'
import {
  Button,
  Card,
  Empty,
  Form,
  Input,
  Modal,
  Popconfirm,
  Select,
  Space,
  Tag,
  Typography,
  message,
} from 'antd'
import {
  CaretRightOutlined,
  DeleteOutlined,
  PlayCircleOutlined,
  PlusOutlined,
  ReloadOutlined,
} from '@ant-design/icons'
import { api } from '../api'
import { useRoute } from '../shell/router'

const RUN_COLOR = {
  pending: 'default',
  running: 'processing',
  paused: 'warning',
  completed: 'success',
  failed: 'error',
}

// ---- Builder modal ---------------------------------------------------------
function WorkflowModal({ open, workflow, agents, onClose, onSaved }) {
  const [form] = Form.useForm()
  const [saving, setSaving] = useState(false)
  const isEdit = !!workflow

  useEffect(() => {
    if (!open) return
    if (workflow) {
      form.setFieldsValue(workflow)
    } else {
      form.resetFields()
      form.setFieldsValue({ stages: [{ name: 'Develop', items: [{}] }] })
    }
  }, [open, workflow])

  const submit = async () => {
    const v = await form.validateFields().catch(() => null)
    if (!v) return
    setSaving(true)
    try {
      if (isEdit) await api.updateWorkflow(workflow.id, v)
      else await api.createWorkflow(v)
      message.success(isEdit ? 'Workflow saved' : 'Workflow created')
      onSaved?.()
      onClose()
    } catch (e) {
      message.error(e.message)
    } finally {
      setSaving(false)
    }
  }

  const agentOptions = agents.map((a) => ({ value: a.name, label: a.name }))

  return (
    <Modal
      title={isEdit ? `Edit ${workflow.name}` : 'New Workflow'}
      open={open}
      onCancel={onClose}
      onOk={submit}
      confirmLoading={saving}
      width={760}
      okText="Save"
    >
      <Form form={form} layout="vertical">
        <Form.Item name="name" label="Name" rules={[{ required: true }]}>
          <Input placeholder="e.g. Canonizer Feature pipeline" />
        </Form.Item>
        <Form.Item name="description" label="Description">
          <Input placeholder="What this pipeline does" />
        </Form.Item>
        <Typography.Text type="secondary">
          Stages run top-to-bottom; agents within a stage run in parallel. Placeholders in a
          prompt: <code>{'{{input}}'}</code> = what you run the workflow on;{' '}
          <code>{'{{prev}}'}</code> = the previous stage's output;{' '}
          <code>{'{{stageN}}'}</code> = stage N's output. Add an optional{' '}
          <b>Tech Lead “Plan”</b> stage first and feed its plan to the dev stages with{' '}
          <code>{'{{prev}}'}</code> — or skip it and control each stage yourself.
        </Typography.Text>
        <Form.List name="stages">
          {(stages, { add: addStage, remove: removeStage }) => (
            <div style={{ marginTop: 10 }}>
              {stages.map((stage, si) => (
                <Card
                  key={stage.key}
                  size="small"
                  style={{ marginBottom: 10 }}
                  title={
                    <Form.Item
                      name={[stage.name, 'name']}
                      noStyle
                      rules={[{ required: true, message: 'Stage name' }]}
                    >
                      <Input
                        size="small"
                        style={{ width: 220 }}
                        prefix={<Tag color="blue" style={{ margin: 0 }}>{`Stage ${si + 1}`}</Tag>}
                        placeholder="Stage name (e.g. Develop)"
                      />
                    </Form.Item>
                  }
                  extra={
                    <Button size="small" danger type="text" icon={<DeleteOutlined />} onClick={() => removeStage(stage.name)} />
                  }
                >
                  <Form.List name={[stage.name, 'items']}>
                    {(items, { add: addItem, remove: removeItem }) => (
                      <>
                        {items.map((item) => (
                          <Space key={item.key} align="start" style={{ display: 'flex', marginBottom: 6 }}>
                            <Form.Item
                              name={[item.name, 'agent']}
                              rules={[{ required: true, message: 'Agent' }]}
                              style={{ marginBottom: 0 }}
                            >
                              <Select
                                showSearch
                                style={{ width: 210 }}
                                placeholder="Agent"
                                options={agentOptions}
                              />
                            </Form.Item>
                            <Form.Item
                              name={[item.name, 'prompt']}
                              rules={[{ required: true, message: 'Prompt' }]}
                              style={{ marginBottom: 0, flex: 1 }}
                            >
                              <Input.TextArea autoSize={{ minRows: 1, maxRows: 4 }} placeholder="What this agent should do — use {{input}}" style={{ width: 400 }} />
                            </Form.Item>
                            <Button type="text" danger icon={<DeleteOutlined />} onClick={() => removeItem(item.name)} />
                          </Space>
                        ))}
                        <Button size="small" type="dashed" icon={<PlusOutlined />} onClick={() => addItem({})}>
                          Add agent to stage
                        </Button>
                      </>
                    )}
                  </Form.List>
                </Card>
              ))}
              <Button type="dashed" block icon={<PlusOutlined />} onClick={() => addStage({ name: '', items: [{}] })}>
                Add stage
              </Button>
            </div>
          )}
        </Form.List>
      </Form>
    </Modal>
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
    <Modal open title={`Run “${workflow?.name}”`} onCancel={onClose} onOk={go} confirmLoading={running} okText="Run pipeline">
      <Typography.Paragraph type="secondary">
        What should this pipeline run on? (A PR number/URL, an issue, or a description — it fills{' '}
        <code>{'{{input}}'}</code> in every stage.)
      </Typography.Paragraph>
      <Input.TextArea rows={3} value={input} onChange={(e) => setInput(e.target.value)} placeholder="e.g. PR 1417 / issue 1807 / 'add a dark-mode toggle'" />
    </Modal>
  )
}

export default function WorkflowsView({ agents = [], isAdmin = false, runWorkflowId = null, onRunHandled }) {
  const [workflows, setWorkflows] = useState([])
  const [presets, setPresets] = useState([])
  const [runs, setRuns] = useState([])
  const [loading, setLoading] = useState(false)
  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState(null)
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

  const addPreset = async (p) => {
    try {
      await api.createWorkflow({ name: p.name, description: p.description, stages: p.stages })
      message.success(`Added “${p.name}”`)
      refresh()
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

  return (
    <>
      <Space style={{ marginBottom: 12 }} wrap>
        {isAdmin && (
          <Button type="primary" icon={<PlusOutlined />} onClick={() => { setEditing(null); setModalOpen(true) }}>
            New Workflow
          </Button>
        )}
        <Button icon={<ReloadOutlined />} onClick={refresh}>Refresh</Button>
        <Typography.Text type="secondary">
          Saved pipelines — ordered stages of agents. Auto-advances on success, halts on failure, pauses at gated stages.
        </Typography.Text>
      </Space>
      {isAdmin && presets.length > 0 && (
        <Space wrap style={{ marginBottom: 12 }}>
          <Typography.Text type="secondary">Templates:</Typography.Text>
          {presets.map((p) => (
            <Button key={p.name} size="small" icon={<PlusOutlined />} onClick={() => addPreset(p)} title={p.description}>
              {p.name}
            </Button>
          ))}
        </Space>
      )}

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12 }}>
        {workflows.length === 0 && !loading && <Empty description={isAdmin ? 'No workflows yet — add one or start from a template.' : 'No workflows yet.'} />}
        {workflows.map((w) => (
          <Card
            key={w.id}
            size="small"
            style={{ width: 340 }}
            title={w.name}
            extra={
              <Space size={4}>
                <Button size="small" type="primary" icon={<PlayCircleOutlined />} onClick={() => setRunWf(w)}>Run</Button>
                {isAdmin && <Button size="small" onClick={() => { setEditing(w); setModalOpen(true) }}>Edit</Button>}
                {isAdmin && (
                  <Popconfirm title="Delete workflow?" onConfirm={() => remove(w.id)}>
                    <Button size="small" danger icon={<DeleteOutlined />} />
                  </Popconfirm>
                )}
              </Space>
            }
          >
            {w.description && <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>{w.description}</Typography.Paragraph>}
            <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 4 }}>
              {w.stages.map((st, i) => (
                <span key={i} style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                  <Tag style={{ margin: 0 }}>
                    {st.name}
                    {st.items.length > 1 ? ` ×${st.items.length}` : ''}
                  </Tag>
                  {i < w.stages.length - 1 && <CaretRightOutlined style={{ color: 'var(--co-text-muted)', fontSize: 10 }} />}
                </span>
              ))}
            </div>
          </Card>
        ))}
      </div>

      {runs.length > 0 && (
        <>
          <Typography.Title level={5} style={{ marginTop: 20 }}>Recent runs</Typography.Title>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {runs.slice(0, 10).map((r) => (
              <a
                key={r.id}
                href={`#/runs/${encodeURIComponent(r.id)}`}
                data-testid="workflow-run-link"
                style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '6px 10px', border: '1px solid var(--co-border)', borderRadius: 6, color: 'inherit' }}
              >
                <Tag color={RUN_COLOR[r.status]} style={{ margin: 0, minWidth: 86, textAlign: 'center' }}>{r.status}</Tag>
                <b>{r.name}</b>
                <Typography.Text type="secondary" style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {r.input}
                </Typography.Text>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  stage {Math.min(r.current_stage + 1, r.stages.length)}/{r.stages.length}
                </Typography.Text>
              </a>
            ))}
          </div>
        </>
      )}

      <WorkflowModal open={modalOpen} workflow={editing} agents={agents} onClose={() => setModalOpen(false)} onSaved={refresh} />
      {runWf && <RunDialog workflow={runWf} onClose={() => setRunWf(null)} onRan={(id) => navigate({ page: 'runs', id })} />}
    </>
  )
}
