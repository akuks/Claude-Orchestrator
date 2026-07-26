import { useCallback, useEffect, useState } from 'react'
import {
  Button,
  Drawer,
  Empty,
  Form,
  Input,
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
} from '@ant-design/icons'
import dayjs from 'dayjs'
import { api } from '../api'

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
        <Form.Item name="default_model" label="Default model">
          <Select options={MODEL_OPTS} />
        </Form.Item>
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

export default function ProjectsView({ onProjectsChanged }) {
  const [projects, setProjects] = useState([])
  const [loading, setLoading] = useState(false)
  const [modalOpen, setModalOpen] = useState(false)
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
    </>
  )
}
