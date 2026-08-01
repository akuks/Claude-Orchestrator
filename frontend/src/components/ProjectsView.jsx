import { useCallback, useEffect, useState } from 'react'
import {
  Button,
  Drawer,
  Empty,
  Form,
  Input,
  InputNumber,
  List,
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
  BookOutlined,
  RadarChartOutlined,
} from '@ant-design/icons'
import dayjs from 'dayjs'
import relativeTime from 'dayjs/plugin/relativeTime'
import { api } from '../api'

dayjs.extend(relativeTime)

const MODEL_OPTS = [
  { value: 'sonnet', label: 'Sonnet' },
  { value: 'opus', label: 'Opus' },
  { value: 'haiku', label: 'Haiku' },
]

function ProjectModal({ open, project, onClose, onSaved }) {
  const [form] = Form.useForm()
  const [saving, setSaving] = useState(false)
  const isEdit = !!project

  useEffect(() => {
    if (!open) return
    if (project) {
      form.setFieldsValue({
        name: project.name,
        directory: project.directory,
        default_model: project.default_model,
        instructions: project.instructions,
        memory_enabled: project.memory_enabled,
        archived: project.archived,
        budget_usd: project.budget_usd,
      })
    } else {
      form.resetFields()
      form.setFieldsValue({ default_model: 'sonnet', memory_enabled: true })
    }
  }, [open, project])

  const submit = async () => {
    let v
    try {
      v = await form.validateFields()
    } catch {
      return
    }
    setSaving(true)
    try {
      if (isEdit) await api.updateProject(project.id, v)
      else await api.createProject(v)
      message.success(isEdit ? 'Project saved' : 'Project created')
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
      title={isEdit ? `Edit ${project.name}` : 'New Project'}
      open={open}
      onCancel={onClose}
      onOk={submit}
      confirmLoading={saving}
      width={560}
    >
      <Form form={form} layout="vertical">
        <Form.Item name="name" label="Name" rules={[{ required: true }]}>
          <Input placeholder="e.g. OpsMind" />
        </Form.Item>
        <Form.Item
          name="directory"
          label="Directory"
          extra="Absolute path to the repo/folder tasks run in. Defaults to ./projects/<slug>."
        >
          <Input placeholder="/Users/you/code/opsmind" disabled={isEdit} />
        </Form.Item>
        <div style={{ display: 'flex', gap: 12 }}>
          <Form.Item name="default_model" label="Default model" style={{ flex: 1 }}>
            <Select options={MODEL_OPTS} />
          </Form.Item>
          <Form.Item
            name="budget_usd"
            label="Budget (USD)"
            style={{ flex: 1 }}
            extra="Flags the project over budget in Usage."
          >
            <InputNumber min={0} step={1} prefix="$" style={{ width: '100%' }} placeholder="none" />
          </Form.Item>
        </div>
        <Form.Item
          name="instructions"
          label="Project instructions"
          extra="Injected into every task's context (does not overwrite the repo's CLAUDE.md)."
        >
          <Input.TextArea rows={3} placeholder="Conventions, constraints, context…" />
        </Form.Item>
        <Space size="large">
          <Form.Item name="memory_enabled" label="Living memory" valuePropName="checked">
            <Switch />
          </Form.Item>
          {isEdit && (
            <Form.Item name="archived" label="Archived" valuePropName="checked">
              <Switch />
            </Form.Item>
          )}
        </Space>
      </Form>
    </Modal>
  )
}

function MemoryDrawer({ project, onClose, onSaved }) {
  const [mem, setMem] = useState('')
  const [summaries, setSummaries] = useState([])
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!project) return
    api.getMemory(project.id).then((m) => setMem(m.memory || ''))
    api.listSummaries(project.id).then(setSummaries)
  }, [project])

  const save = async () => {
    setBusy(true)
    try {
      await api.updateProject(project.id, { memory: mem })
      message.success('Memory saved')
      onSaved?.()
    } catch (e) {
      message.error(e.message)
    } finally {
      setBusy(false)
    }
  }

  const regenerate = async () => {
    setBusy(true)
    try {
      const m = await api.regenerateMemory(project.id)
      setMem(m.memory || '')
      message.success('Memory regenerated from task history')
      onSaved?.()
    } catch (e) {
      message.error(e.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Drawer
      open={!!project}
      onClose={onClose}
      width={640}
      title={project ? `Memory — ${project.name}` : ''}
      extra={
        <Space>
          <Button icon={<ReloadOutlined />} loading={busy} onClick={regenerate}>
            Regenerate
          </Button>
          <Button type="primary" loading={busy} onClick={save}>
            Save
          </Button>
        </Space>
      }
    >
      <Typography.Paragraph type="secondary">
        Living memory is auto-updated after each task. You can edit it directly, or
        regenerate it from the project's task history.
      </Typography.Paragraph>
      <Input.TextArea
        value={mem}
        onChange={(e) => setMem(e.target.value)}
        autoSize={{ minRows: 8, maxRows: 20 }}
        style={{ fontFamily: 'ui-monospace, Menlo, monospace', fontSize: 13 }}
      />
      <Typography.Title level={5} style={{ marginTop: 20 }}>
        Task summaries ({summaries.length})
      </Typography.Title>
      {summaries.length ? (
        <List
          size="small"
          dataSource={summaries}
          renderItem={(sm) => (
            <List.Item>
              <List.Item.Meta
                title={sm.title}
                description={sm.summary}
              />
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                {dayjs(sm.created_at).format('MMM D, HH:mm')}
              </Typography.Text>
            </List.Item>
          )}
        />
      ) : (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="No task history yet" />
      )}
    </Drawer>
  )
}

function DiscoverModal({ open, onClose, onImported }) {
  const [loading, setLoading] = useState(false)
  const [candidates, setCandidates] = useState([])
  const [selected, setSelected] = useState([])
  const [importing, setImporting] = useState(false)

  useEffect(() => {
    if (!open) return
    setSelected([])
    setLoading(true)
    api
      .discoverProjects()
      .then(setCandidates)
      .catch((e) => message.error(e.message))
      .finally(() => setLoading(false))
  }, [open])

  const doImport = async () => {
    setImporting(true)
    try {
      const created = await api.importProjects(selected)
      message.success(`Added ${created.length} project${created.length === 1 ? '' : 's'}`)
      onImported?.()
      onClose()
    } catch (e) {
      message.error(e.message)
    } finally {
      setImporting(false)
    }
  }

  const addable = candidates.filter((c) => !c.already_added)

  return (
    <Modal
      title="Discover Claude projects"
      open={open}
      onCancel={onClose}
      width={760}
      footer={[
        <Button key="c" onClick={onClose}>
          Cancel
        </Button>,
        <Button
          key="a"
          type="primary"
          loading={importing}
          disabled={!selected.length}
          onClick={doImport}
        >
          Add {selected.length || ''} selected
        </Button>,
      ]}
    >
      <Typography.Paragraph type="secondary">
        Directories where you've run Claude Code (from <code>~/.claude/projects</code>).
        Pick the ones to manage as orchestrator projects.
      </Typography.Paragraph>
      <Table
        rowKey="directory"
        size="small"
        loading={loading}
        dataSource={candidates}
        pagination={false}
        scroll={{ y: 380 }}
        rowSelection={{
          selectedRowKeys: selected,
          onChange: setSelected,
          getCheckboxProps: (row) => ({ disabled: row.already_added }),
        }}
        columns={[
          {
            title: 'Project',
            dataIndex: 'name',
            render: (n, row) => (
              <Space>
                <b>{n}</b>
                {row.already_added && <Tag color="success">added</Tag>}
              </Space>
            ),
          },
          {
            title: 'Directory',
            dataIndex: 'directory',
            ellipsis: true,
            render: (d) => <Typography.Text type="secondary">{d}</Typography.Text>,
          },
          { title: 'Sessions', dataIndex: 'sessions', width: 80 },
          {
            title: 'Last used',
            dataIndex: 'last_active',
            width: 110,
            render: (d) => dayjs(d).fromNow(),
          },
        ]}
      />
      {!loading && !addable.length && (
        <Typography.Text type="secondary">
          Nothing new to add — all discovered projects are already managed.
        </Typography.Text>
      )}
    </Modal>
  )
}

export default function ProjectsView({ onProjectsChanged }) {
  const [projects, setProjects] = useState([])
  const [loading, setLoading] = useState(false)
  const [modalOpen, setModalOpen] = useState(false)
  const [discoverOpen, setDiscoverOpen] = useState(false)
  const [editing, setEditing] = useState(null)
  const [memoryProject, setMemoryProject] = useState(null)

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      setProjects(await api.listProjects(true))
      onProjectsChanged?.()
    } catch (e) {
      message.error(e.message)
    } finally {
      setLoading(false)
    }
  }, [onProjectsChanged])

  useEffect(() => {
    refresh()
  }, [refresh])

  const remove = async (id) => {
    try {
      await api.deleteProject(id)
      message.success('Project deleted')
      refresh()
    } catch (e) {
      message.error(e.message)
    }
  }

  const columns = [
    {
      title: 'Project',
      dataIndex: 'name',
      render: (n, row) => (
        <Space>
          <b>{n}</b>
          {row.archived && <Tag>archived</Tag>}
          {!row.memory_enabled && <Tag color="default">memory off</Tag>}
        </Space>
      ),
    },
    {
      title: 'Directory',
      dataIndex: 'directory',
      ellipsis: true,
      render: (d) => <Typography.Text type="secondary">{d}</Typography.Text>,
    },
    { title: 'Model', dataIndex: 'default_model', width: 90 },
    { title: 'Tasks', dataIndex: 'task_count', width: 70 },
    {
      title: 'Cost',
      dataIndex: 'total_cost_usd',
      width: 90,
      render: (c) => (c ? `$${c.toFixed(4)}` : '—'),
    },
    {
      title: 'Actions',
      key: 'actions',
      width: 220,
      render: (_, row) => (
        <Space size="small">
          <Button size="small" icon={<BookOutlined />} onClick={() => setMemoryProject(row)}>
            Memory
          </Button>
          <Button
            size="small"
            icon={<EditOutlined />}
            onClick={() => {
              setEditing(row)
              setModalOpen(true)
            }}
          />
          <Popconfirm
            title="Delete this project? Task history is removed; the directory is left untouched."
            onConfirm={() => remove(row.id)}
          >
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
          New Project
        </Button>
        <Button icon={<RadarChartOutlined />} onClick={() => setDiscoverOpen(true)}>
          Discover
        </Button>
        <Button icon={<ReloadOutlined />} onClick={refresh}>
          Refresh
        </Button>
      </Space>

      <Table
        rowKey="id"
        loading={loading}
        columns={columns}
        dataSource={projects}
        pagination={false}
        locale={{ emptyText: <Empty description="No projects yet" /> }}
      />

      <ProjectModal
        open={modalOpen}
        project={editing}
        onClose={() => setModalOpen(false)}
        onSaved={refresh}
      />
      <MemoryDrawer
        project={memoryProject}
        onClose={() => setMemoryProject(null)}
        onSaved={refresh}
      />
      <DiscoverModal
        open={discoverOpen}
        onClose={() => setDiscoverOpen(false)}
        onImported={refresh}
      />
    </>
  )
}
