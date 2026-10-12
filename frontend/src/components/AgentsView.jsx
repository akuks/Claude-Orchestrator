import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Button,
  Collapse,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Segmented,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Typography,
  Upload,
  message,
} from 'antd'
import {
  DeleteOutlined,
  EditOutlined,
  FolderOutlined,
  PlusOutlined,
  ReloadOutlined,
  ThunderboltOutlined,
  UploadOutlined,
} from '@ant-design/icons'
import { api } from '../api'

const MODEL_OPTS = [
  { value: 'sonnet', label: 'Sonnet' },
  { value: 'opus', label: 'Opus' },
  { value: 'haiku', label: 'Haiku' },
]

function AgentModal({ open, agent, seed, projects, onClose, onSaved }) {
  const [form] = Form.useForm()
  const [saving, setSaving] = useState(false)
  const isEdit = !!agent

  useEffect(() => {
    if (!open) return
    if (agent) {
      form.setFieldsValue({ ...agent, model: agent.model || undefined })
    } else {
      form.resetFields()
      // Seed from a template (role + governance) when creating from a preset.
      form.setFieldsValue({ priority: 'normal', max_turns: 25, ...(seed || {}) })
    }
  }, [open, agent, seed])

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
      title={isEdit ? `Edit ${agent.name}` : seed ? `New agent from “${seed.name}” template` : 'New Agent'}
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
        <Form.Item
          name="folder"
          label="Folder"
          extra="Organise the list, e.g. “Canonizer/Dev”. Use “/” for subfolders. Blank = Ungrouped."
        >
          <Input placeholder="Canonizer/Dev" allowClear />
        </Form.Item>
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

// Read a File into base64 (strips the data: URL prefix).
function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const r = new FileReader()
    r.onload = () => resolve(String(r.result).split(',')[1])
    r.onerror = reject
    r.readAsDataURL(file)
  })
}

// Turn an uploaded/pasted task list into individual task prompts: one per line,
// stripping markdown list/checkbox/number markers and skipping blanks/headings.
function parseTaskList(text) {
  return (text || '')
    .split('\n')
    .map((l) => l.trim())
    .filter((l) => l && !l.startsWith('#'))
    .map((l) =>
      l
        .replace(/^[-*+]\s+/, '')
        .replace(/^\d+[.)]\s+/, '')
        .replace(/^\[[ xX]\]\s+/, '')
        .trim()
    )
    .filter(Boolean)
}

function RunModal({ agent, projects, onClose, onRan }) {
  const [form] = Form.useForm()
  const [running, setRunning] = useState(false)
  const [mode, setMode] = useState('single')
  const [files, setFiles] = useState([]) // single-run attachments
  const [bulkText, setBulkText] = useState('')
  const [projectId, setProjectId] = useState(undefined)

  useEffect(() => {
    if (agent) {
      form.setFieldsValue({ prompt: agent.default_prompt })
      setProjectId(agent.project_id || undefined)
      setMode('single')
      setFiles([])
      setBulkText('')
    }
  }, [agent])

  const projectOptions = projects
    .filter((p) => !p.archived)
    .map((p) => ({ value: p.id, label: p.name }))
  const tasks = useMemo(() => parseTaskList(bulkText), [bulkText])

  const runSingle = async () => {
    const v = await form.validateFields().catch(() => null)
    if (!v) return
    setRunning(true)
    try {
      const input_files = await Promise.all(
        files.map(async (f) => ({
          name: f.name,
          content_base64: await fileToBase64(f.originFileObj || f),
        }))
      )
      const task = await api.runAgent(agent.id, {
        prompt: v.prompt || undefined,
        project_id: projectId || undefined,
        input_files,
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

  const runBulk = async () => {
    if (tasks.length === 0) {
      message.warning('No tasks found — add one task per line.')
      return
    }
    setRunning(true)
    try {
      const created = await api.bulkRunAgent(agent.id, {
        prompts: tasks,
        project_id: projectId || undefined,
      })
      message.success(`Created ${created.length} task${created.length === 1 ? '' : 's'}`)
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
      onOk={mode === 'single' ? runSingle : runBulk}
      confirmLoading={running}
      okText={mode === 'single' ? 'Run' : `Create ${tasks.length} task${tasks.length === 1 ? '' : 's'}`}
      okButtonProps={{ disabled: mode === 'bulk' && tasks.length === 0 }}
      width={620}
    >
      <Segmented
        block
        value={mode}
        onChange={setMode}
        options={[
          { label: 'Single task', value: 'single' },
          { label: 'Bulk (task list)', value: 'bulk' },
        ]}
        style={{ marginBottom: 12 }}
      />
      <Form form={form} layout="vertical">
        {mode === 'single' ? (
          <>
            <Form.Item
              name="prompt"
              label="Task"
              rules={[{ required: true, message: 'Give the agent something to do' }]}
            >
              <Input.TextArea rows={3} placeholder="What should the agent do?" />
            </Form.Item>
            <Form.Item
              label="Attach files"
              extra="Specs, tickets, sample data — the agent reads them for this task."
            >
              <Upload
                multiple
                beforeUpload={() => false}
                fileList={files}
                onChange={({ fileList }) => setFiles(fileList)}
              >
                <Button icon={<UploadOutlined />}>Attach files</Button>
              </Upload>
            </Form.Item>
          </>
        ) : (
          <Form.Item
            label="Task list"
            extra="One task per line (markdown lists / checkboxes are fine). Each line becomes its own task the agent runs."
          >
            <Upload
              maxCount={1}
              accept=".md,.txt,.csv"
              showUploadList={false}
              beforeUpload={async (file) => {
                setBulkText(await file.text())
                return false
              }}
            >
              <Button icon={<UploadOutlined />}>Upload a task file</Button>
            </Upload>
            <Input.TextArea
              rows={7}
              value={bulkText}
              onChange={(e) => setBulkText(e.target.value)}
              placeholder={'- Fix null crash on /topic\n- Add /health endpoint\n- Rate-limit /register'}
              style={{ marginTop: 8 }}
            />
            <Typography.Text type="secondary">
              {tasks.length} task{tasks.length === 1 ? '' : 's'} will be created
              {agent?.requires_approval ? ' (each needs approval)' : ''}.
            </Typography.Text>
          </Form.Item>
        )}
        <Form.Item label="Project" extra="Override the agent's default for this run.">
          <Select
            allowClear
            placeholder="No project"
            value={projectId}
            onChange={setProjectId}
            options={projectOptions}
          />
        </Form.Item>
      </Form>
    </Modal>
  )
}

// Build a folder tree from agents' slash-separated `folder` paths.
function buildTree(agents) {
  const root = { children: {}, agents: [] }
  for (const a of agents) {
    const segs = (a.folder || '').split('/').map((s) => s.trim()).filter(Boolean)
    let node = root
    for (const seg of segs) {
      node.children[seg] = node.children[seg] || { children: {}, agents: [] }
      node = node.children[seg]
    }
    node.agents.push(a)
  }
  // Ungrouped agents (no folder) become their own top-level group.
  if (root.agents.length) {
    root.children['Ungrouped'] = { children: {}, agents: root.agents }
    root.agents = []
  }
  return root
}

function countAgents(node) {
  return (
    node.agents.length +
    Object.values(node.children).reduce((s, c) => s + countAgents(c), 0)
  )
}

// Recursively render folders as nested collapsible panels.
function FolderGroups({ node, columns, path = '' }) {
  const names = Object.keys(node.children).sort((a, b) => a.localeCompare(b))
  if (names.length === 0) return null
  const items = names.map((name) => {
    const child = node.children[name]
    const key = `${path}/${name}`
    return {
      key,
      label: (
        <span>
          <FolderOutlined style={{ marginRight: 6 }} />
          {name}{' '}
          <Typography.Text type="secondary">({countAgents(child)})</Typography.Text>
        </span>
      ),
      children: (
        <>
          {child.agents.length > 0 && (
            <Table
              rowKey="id"
              size="small"
              showHeader={false}
              columns={columns}
              dataSource={child.agents}
              pagination={false}
            />
          )}
          {Object.keys(child.children).length > 0 && (
            <FolderGroups node={child} columns={columns} path={key} />
          )}
        </>
      ),
    }
  })
  return (
    <Collapse
      ghost
      size="small"
      items={items}
      defaultActiveKey={names.map((n) => `${path}/${n}`)}
    />
  )
}

export default function AgentsView({ projects = [] }) {
  const [agents, setAgents] = useState([])
  const [presets, setPresets] = useState([])
  const [loading, setLoading] = useState(false)
  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState(null)
  const [seed, setSeed] = useState(null)
  const [runAgent, setRunAgent] = useState(null)

  const projName = (id) => projects.find((p) => p.id === id)?.name
  const tree = useMemo(() => buildTree(agents), [agents])

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

  // Open the Agent form pre-filled from a template (role + governance) so you can
  // pick the project and review before creating.
  const addPreset = (p) => {
    setEditing(null)
    setSeed({
      name: p.name,
      description: p.description,
      system_prompt: p.system_prompt,
      default_prompt: p.default_prompt,
      model: p.model || undefined,
      max_turns: p.max_turns ?? 25,
      max_budget_usd: p.max_budget_usd ?? undefined,
      priority: p.priority || 'normal',
      requires_approval: p.requires_approval ?? false,
      allow_remote: p.allow_remote ?? false,
      folder: p.category || '',
      tags: p.tags || [],
    })
    setModalOpen(true)
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
              setSeed(null)
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
            setSeed(null)
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
        <div style={{ marginBottom: 12 }}>
          <Typography.Text strong style={{ display: 'block', marginBottom: 6 }}>
            Agent templates
            <Typography.Text type="secondary" style={{ fontWeight: 400 }}>
              {' '}— pick one to create a governed agent (roles, not runnable tasks)
            </Typography.Text>
          </Typography.Text>
          {Object.entries(
            presets.reduce((groups, p) => {
              const cat = p.category || 'Templates'
              ;(groups[cat] = groups[cat] || []).push(p)
              return groups
            }, {})
          ).map(([cat, items]) => (
            <Space wrap key={cat} style={{ marginBottom: 6, display: 'flex' }}>
              <Typography.Text type="secondary" style={{ width: 90, display: 'inline-block' }}>
                {cat}:
              </Typography.Text>
              {items.map((p) => (
                <Button
                  key={p.name}
                  size="small"
                  icon={<PlusOutlined />}
                  onClick={() => addPreset(p)}
                  title={p.description}
                >
                  {p.name}
                  {p.requires_approval && (
                    <Tag color="warning" style={{ marginLeft: 6, marginRight: 0 }}>
                      gated
                    </Tag>
                  )}
                  {p.allow_remote && (
                    <Tag color="geekblue" style={{ marginLeft: 6, marginRight: 0 }}>
                      SSH
                    </Tag>
                  )}
                </Button>
              ))}
            </Space>
          ))}
        </div>
      )}
      {loading ? (
        <Table rowKey="id" loading columns={columns} dataSource={[]} pagination={false} />
      ) : (
        <FolderGroups node={tree} columns={columns} />
      )}
      <AgentModal
        open={modalOpen}
        agent={editing}
        seed={seed}
        projects={projects}
        onClose={() => {
          setModalOpen(false)
          setSeed(null)
        }}
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
