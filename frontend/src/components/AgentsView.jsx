import { useCallback, useEffect, useState } from 'react'
import {
  Button,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import {
  DeleteOutlined,
  EditOutlined,
  PlusOutlined,
  ReloadOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons'
import { api } from '../api'

const MODEL_OPTS = [
  { value: 'sonnet', label: 'Sonnet' },
  { value: 'opus', label: 'Opus' },
  { value: 'haiku', label: 'Haiku' },
]

function AgentModal({ open, agent, projects, onClose, onSaved }) {
  const [form] = Form.useForm()
  const [saving, setSaving] = useState(false)
  const isEdit = !!agent

  useEffect(() => {
    if (!open) return
    if (agent) {
      form.setFieldsValue({ ...agent, model: agent.model || undefined })
    } else {
      form.resetFields()
      form.setFieldsValue({ priority: 'normal', max_turns: 25 })
    }
  }, [open, agent])

  const submit = async () => {
    let v
    try {
      v = await form.validateFields()
    } catch {
      return
    }
    setSaving(true)
    try {
      const payload = { ...v, model: v.model || undefined }
      if (isEdit) await api.updateAgent(agent.id, payload)
      else await api.createAgent(payload)
      message.success(isEdit ? 'Agent saved' : 'Agent created')
      onSaved?.()
      onClose()
    } catch (e) {
      message.error(e.message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal
      title={isEdit ? `Edit ${agent.name}` : 'New Agent'}
      open={open}
      onCancel={onClose}
      onOk={submit}
      confirmLoading={saving}
      width={640}
    >
      <Form form={form} layout="vertical">
        <Form.Item name="name" label="Name" rules={[{ required: true }]}>
          <Input placeholder="e.g. Security Auditor" />
        </Form.Item>
        <Form.Item name="description" label="Description">
          <Input placeholder="What this agent is for" />
        </Form.Item>
        <Form.Item
          name="system_prompt"
          label="Role (system prompt)"
          extra="The agent's persistent persona/instructions — applied to every run via --append-system-prompt."
          rules={[{ required: true, message: 'Give the agent a role' }]}
        >
          <Input.TextArea rows={3} placeholder="You are a meticulous security auditor. Follow OWASP…" />
        </Form.Item>
        <Form.Item
          name="default_prompt"
          label="Default task (optional)"
          extra="Default input if none is given at run time."
        >
          <Input.TextArea rows={2} placeholder="e.g. Audit the changed files on this branch" />
        </Form.Item>
        <div style={{ display: 'flex', gap: 12 }}>
          <Form.Item
            name="project_id"
            label="Default project (optional)"
            style={{ flex: 1 }}
            extra="Overridable at run time."
          >
            <Select
              allowClear
              placeholder="No default (pick at run)"
              options={projects.filter((p) => !p.archived).map((p) => ({ value: p.id, label: p.name }))}
            />
          </Form.Item>
          <Form.Item name="model" label="Model" style={{ width: 130 }}>
            <Select allowClear placeholder="Default" options={MODEL_OPTS} />
          </Form.Item>
        </div>
        <div style={{ display: 'flex', gap: 12 }}>
          <Form.Item name="priority" label="Priority" style={{ flex: 1 }}>
            <Select options={['low', 'normal', 'high', 'urgent'].map((p) => ({ value: p, label: p }))} />
          </Form.Item>
          <Form.Item name="max_turns" label="Max turns" style={{ width: 120 }}>
            <InputNumber min={1} max={200} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item name="max_budget_usd" label="Budget cap" style={{ width: 120 }}>
            <InputNumber min={0} step={0.5} prefix="$" style={{ width: '100%' }} placeholder="none" />
          </Form.Item>
        </div>
        <Form.Item name="tags" label="Tags">
          <Select mode="tags" placeholder="tags" tokenSeparators={[',']} />
        </Form.Item>
        <Form.Item
          name="requires_approval"
          label="Approval-gate every run"
          valuePropName="checked"
          extra="Each run lands in Approvals before it executes — for agents that change state (e.g. remediation)."
        >
          <Switch />
        </Form.Item>
        <Form.Item
          name="allow_remote"
          label="Allow remote login (SSH)"
          valuePropName="checked"
          extra="Off by default. When off, ssh/scp/sftp are blocked for this agent's tasks. Enable only for remote-ops agents."
        >
          <Switch />
        </Form.Item>
      </Form>
    </Modal>
  )
}

function RunModal({ agent, projects, onClose, onRan }) {
  const [form] = Form.useForm()
  const [running, setRunning] = useState(false)

  useEffect(() => {
    if (agent) {
      form.setFieldsValue({ prompt: agent.default_prompt, project_id: agent.project_id })
    }
  }, [agent])

  const run = async () => {
    const v = await form.validateFields().catch(() => null)
    if (!v) return
    setRunning(true)
    try {
      const task = await api.runAgent(agent.id, {
        prompt: v.prompt || undefined,
        project_id: v.project_id || undefined,
      })
      message.success(
        task.status === 'awaiting_approval' ? 'Queued for approval' : 'Agent run started'
      )
      onRan?.()
      onClose()
    } catch (e) {
      message.error(e.message)
    } finally {
      setRunning(false)
    }
  }

  return (
    <Modal
      title={agent ? `Run ${agent.name}` : ''}
      open={!!agent}
      onCancel={onClose}
      onOk={run}
      confirmLoading={running}
      okText="Run"
    >
      <Form form={form} layout="vertical">
        <Form.Item
          name="prompt"
          label="Task"
          rules={[{ required: true, message: 'Give the agent something to do' }]}
        >
          <Input.TextArea rows={3} placeholder="What should the agent do?" />
        </Form.Item>
        <Form.Item name="project_id" label="Project" extra="Override the agent's default for this run.">
          <Select
            allowClear
            placeholder="No project"
            options={projects.filter((p) => !p.archived).map((p) => ({ value: p.id, label: p.name }))}
          />
        </Form.Item>
      </Form>
    </Modal>
  )
}

export default function AgentsView({ projects = [] }) {
  const [agents, setAgents] = useState([])
  const [presets, setPresets] = useState([])
  const [loading, setLoading] = useState(false)
  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState(null)
  const [runAgent, setRunAgent] = useState(null)

  const projName = (id) => projects.find((p) => p.id === id)?.name

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const [a, p] = await Promise.all([api.listAgents(), api.agentPresets()])
      setAgents(a)
      setPresets(p)
    } catch (e) {
      message.error(e.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  const addPreset = async (p) => {
    try {
      await api.createAgent({
        name: p.name,
        description: p.description,
        system_prompt: p.system_prompt,
        default_prompt: p.default_prompt,
        tags: p.tags || [],
      })
      message.success(`Added agent “${p.name}”`)
      refresh()
    } catch (e) {
      message.error(e.message)
    }
  }

  const remove = async (id) => {
    try {
      await api.deleteAgent(id)
      message.success('Agent deleted')
      refresh()
    } catch (e) {
      message.error(e.message)
    }
  }

  const columns = [
    {
      title: 'Agent',
      dataIndex: 'name',
      render: (n, row) => (
        <div>
          <b>{n}</b>
          {row.requires_approval && (
            <Tag color="warning" style={{ marginLeft: 6 }}>
              approval-gated
            </Tag>
          )}
          {row.allow_remote && (
            <Tag color="geekblue" style={{ marginLeft: 6 }}>
              SSH
            </Tag>
          )}
          {row.description && (
            <div style={{ fontSize: 12, color: '#8a7a6d' }}>{row.description}</div>
          )}
        </div>
      ),
    },
    {
      title: 'Default project',
      dataIndex: 'project_id',
      width: 150,
      render: (id) => (id ? <Tag>{projName(id) || id.slice(0, 6)}</Tag> : <span style={{ color: '#8a7a6d' }}>any</span>),
    },
    { title: 'Model', dataIndex: 'model', width: 90, render: (m) => m || 'default' },
    {
      title: 'Budget',
      dataIndex: 'max_budget_usd',
      width: 90,
      render: (b) => (b ? `$${b}` : '—'),
    },
    {
      title: 'Actions',
      key: 'a',
      width: 210,
      render: (_, row) => (
        <Space size="small">
          <Button
            type="primary"
            size="small"
            icon={<ThunderboltOutlined />}
            onClick={() => setRunAgent(row)}
          >
            Run
          </Button>
          <Button
            size="small"
            icon={<EditOutlined />}
            onClick={() => {
              setEditing(row)
              setModalOpen(true)
            }}
          />
          <Popconfirm title="Delete this agent?" onConfirm={() => remove(row.id)}>
            <Button size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <>
      <Space style={{ marginBottom: 12 }}>
        <Button
          type="primary"
          icon={<PlusOutlined />}
          onClick={() => {
            setEditing(null)
            setModalOpen(true)
          }}
        >
          New Agent
        </Button>
        <Button icon={<ReloadOutlined />} onClick={refresh}>
          Refresh
        </Button>
        <Typography.Text type="secondary">
          Reusable, governed roles — a system prompt + model + budget + optional project.
        </Typography.Text>
      </Space>
      {presets.length > 0 && (
        <Space wrap style={{ marginBottom: 12 }}>
          <Typography.Text type="secondary">Starter agents:</Typography.Text>
          {presets.map((p) => (
            <Button key={p.name} size="small" icon={<PlusOutlined />} onClick={() => addPreset(p)} title={p.description}>
              {p.name}
            </Button>
          ))}
        </Space>
      )}
      <Table
        rowKey="id"
        size="middle"
        loading={loading}
        columns={columns}
        dataSource={agents}
        pagination={false}
      />
      <AgentModal
        open={modalOpen}
        agent={editing}
        projects={projects}
        onClose={() => setModalOpen(false)}
        onSaved={refresh}
      />
      <RunModal
        agent={runAgent}
        projects={projects}
        onClose={() => setRunAgent(null)}
        onRan={refresh}
      />
    </>
  )
}
