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
  CheckCircleFilled,
  ClockCircleFilled,
  CloseCircleFilled,
  DeleteOutlined,
  ExclamationCircleFilled,
  LoadingOutlined,
  MinusCircleFilled,
  PlayCircleOutlined,
  PlusOutlined,
  ReloadOutlined,
} from '@ant-design/icons'
import { api } from '../api'

const TASK_ICON = {
  completed: { icon: <CheckCircleFilled />, color: '#52c41a' },
  failed: { icon: <CloseCircleFilled />, color: '#ff4d4f' },
  running: { icon: <LoadingOutlined spin />, color: '#1677ff' },
  queued: { icon: <ClockCircleFilled />, color: '#8c8c8c' },
  awaiting_approval: { icon: <ExclamationCircleFilled />, color: '#faad14' },
  cancelled: { icon: <MinusCircleFilled />, color: '#8c8c8c' },
}
const RUN_COLOR = {
  pending: 'default',
  running: 'processing',
  paused: 'warning',
  completed: 'success',
  failed: 'error',
}
const fmtCost = (c) => (c == null ? null : `$${Number(c).toFixed(2)}`)

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
          Stages run top-to-bottom. Agents within a stage run in parallel. Use{' '}
          <code>{'{{input}}'}</code> in a prompt — it's replaced with what you run the workflow on
          (a PR, issue, or description).
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

// ---- Run board (live) ------------------------------------------------------
function RunBoard({ runId, onClose }) {
  const [data, setData] = useState(null)

  const refresh = useCallback(async () => {
    try {
      setData(await api.getWorkflowRun(runId))
    } catch (e) {
      message.error(e.message)
    }
  }, [runId])

  useEffect(() => {
    refresh()
    const id = setInterval(refresh, 3000) // live
    return () => clearInterval(id)
  }, [refresh])

  if (!data) return null
  const { run, tasks } = data
  const stages = run.stages || []

  return (
    <Modal open onCancel={onClose} onOk={onClose} width={720} footer={<Button onClick={onClose}>Close</Button>}
      title={
        <Space>
          {run.name}
          <Tag color={RUN_COLOR[run.status]}>{run.status}</Tag>
          {run.status === 'paused' && (
            <Typography.Text type="warning" style={{ fontSize: 12 }}>
              waiting on approval — approve in the Approvals tab to continue
            </Typography.Text>
          )}
        </Space>
      }
    >
      {run.input && (
        <Typography.Paragraph type="secondary" style={{ marginBottom: 12 }}>
          Running on: {run.input}
        </Typography.Paragraph>
      )}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        {stages.map((st, si) => {
          const stageTasks = tasks.filter((t) => t.stage_index === si)
          const state =
            si < run.current_stage || (run.status === 'completed')
              ? 'done'
              : si === run.current_stage
                ? (run.status === 'failed' ? 'failed' : 'active')
                : 'pending'
          const stateTag = { done: ['success', 'done'], active: ['processing', 'active'], failed: ['error', 'failed'], pending: ['default', 'pending'] }[state]
          return (
            <Card
              key={si}
              size="small"
              title={
                <Space>
                  <Tag color="blue">{`Stage ${si + 1}`}</Tag>
                  <b>{st.name}</b>
                  <Tag color={stateTag[0]}>{stateTag[1]}</Tag>
                  {st.items.length > 1 && <Typography.Text type="secondary" style={{ fontSize: 12 }}>parallel</Typography.Text>}
                </Space>
              }
              style={{ opacity: state === 'pending' ? 0.6 : 1 }}
            >
              {stageTasks.length === 0 ? (
                <Typography.Text type="secondary">
                  {st.items.map((it) => it.agent).join(', ')} — not started
                </Typography.Text>
              ) : (
                stageTasks.map((t) => {
                  const ic = TASK_ICON[t.status] || TASK_ICON.queued
                  const agent = (t.title || '').match(/\]\s*([^:]+):/)?.[1] || ''
                  return (
                    <div key={t.id} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '3px 0' }}>
                      <span style={{ color: ic.color, display: 'inline-flex' }}>{ic.icon}</span>
                      <Tag color="geekblue" style={{ margin: 0 }}>{agent.trim()}</Tag>
                      <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontSize: 13 }}>
                        {(t.title || '').replace(/^\[[^\]]+\]\s*[^:]+:\s*/, '')}
                      </span>
                      {fmtCost(t.total_cost_usd) && (
                        <Typography.Text type="secondary" style={{ fontSize: 11 }}>{fmtCost(t.total_cost_usd)}</Typography.Text>
                      )}
                    </div>
                  )
                })
              )}
            </Card>
          )
        })}
      </div>
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
      <Input.TextArea rows={3} value={input} onChange={(e) => setInput(e.target.value)} placeholder="e.g. PR #1417 / issue #1807 / 'add a dark-mode toggle'" />
    </Modal>
  )
}

export default function WorkflowsView({ agents = [] }) {
  const [workflows, setWorkflows] = useState([])
  const [presets, setPresets] = useState([])
  const [runs, setRuns] = useState([])
  const [loading, setLoading] = useState(false)
  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState(null)
  const [runWf, setRunWf] = useState(null)
  const [boardRun, setBoardRun] = useState(null)

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
    } catch (e) {
      message.error(e.message)
    } finally {
      setLoading(false)
    }
  }, [])

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
        <Button type="primary" icon={<PlusOutlined />} onClick={() => { setEditing(null); setModalOpen(true) }}>
          New Workflow
        </Button>
        <Button icon={<ReloadOutlined />} onClick={refresh}>Refresh</Button>
        <Typography.Text type="secondary">
          Saved pipelines — ordered stages of agents. Auto-advances on success, halts on failure, pauses at gated stages.
        </Typography.Text>
      </Space>
      {presets.length > 0 && (
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
        {workflows.length === 0 && !loading && <Empty description="No workflows yet — add one or start from a template." />}
        {workflows.map((w) => (
          <Card
            key={w.id}
            size="small"
            style={{ width: 340 }}
            title={w.name}
            extra={
              <Space size={4}>
                <Button size="small" type="primary" icon={<PlayCircleOutlined />} onClick={() => setRunWf(w)}>Run</Button>
                <Button size="small" onClick={() => { setEditing(w); setModalOpen(true) }}>Edit</Button>
                <Popconfirm title="Delete workflow?" onConfirm={() => remove(w.id)}>
                  <Button size="small" danger icon={<DeleteOutlined />} />
                </Popconfirm>
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
                  {i < w.stages.length - 1 && <CaretRightOutlined style={{ color: '#8c8c8c', fontSize: 10 }} />}
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
              <div
                key={r.id}
                onClick={() => setBoardRun(r.id)}
                style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '6px 10px', border: '1px solid rgba(150,130,110,0.2)', borderRadius: 6, cursor: 'pointer' }}
              >
                <Tag color={RUN_COLOR[r.status]} style={{ margin: 0, minWidth: 86, textAlign: 'center' }}>{r.status}</Tag>
                <b>{r.name}</b>
                <Typography.Text type="secondary" style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {r.input}
                </Typography.Text>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  stage {Math.min(r.current_stage + 1, r.stages.length)}/{r.stages.length}
                </Typography.Text>
              </div>
            ))}
          </div>
        </>
      )}

      <WorkflowModal open={modalOpen} workflow={editing} agents={agents} onClose={() => setModalOpen(false)} onSaved={refresh} />
      {runWf && <RunDialog workflow={runWf} onClose={() => setRunWf(null)} onRan={(id) => { setBoardRun(id); refresh() }} />}
      {boardRun && <RunBoard runId={boardRun} onClose={() => setBoardRun(null)} />}
    </>
  )
}
