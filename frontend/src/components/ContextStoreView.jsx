import { useCallback, useEffect, useState } from 'react'
import {
  Alert,
  Button,
  Drawer,
  Form,
  Input,
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
import { DeleteOutlined, EditOutlined, EyeOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons'
import { api } from '../api'

function DocModal({ open, doc, projects, isAdmin, onClose, onSaved }) {
  const [form] = Form.useForm()
  const [saving, setSaving] = useState(false)
  const scope = Form.useWatch('scope', form)
  const isEdit = !!doc

  useEffect(() => {
    if (!open) return
    form.resetFields()
    form.setFieldsValue(
      doc
        ? { ...doc, project_id: doc.project_id || undefined }
        : { scope: isAdmin ? 'global' : 'project', enabled: true, path: '', title: '', abstract: '', body: '' }
    )
  }, [open, doc])

  const submit = async () => {
    const v = await form.validateFields().catch(() => null)
    if (!v) return
    setSaving(true)
    const payload = { ...v, project_id: v.scope === 'project' ? v.project_id : null }
    try {
      if (isEdit) await api.updateContextDoc(doc.id, payload)
      else await api.createContextDoc(payload)
      message.success(isEdit ? 'Doc saved' : 'Doc added')
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
      open={open}
      width={760}
      title={isEdit ? `Edit ${doc.path}` : 'Add knowledge doc'}
      onCancel={onClose}
      onOk={submit}
      confirmLoading={saving}
      okText="Save"
    >
      <Form form={form} layout="vertical">
        <Space.Compact block>
          <Form.Item
            name="path"
            label="Path"
            style={{ flex: 2 }}
            rules={[{ required: true, message: 'e.g. runbooks/deploy.md' }]}
            extra="Folders organise docs, e.g. runbooks/deploy.md, conventions/api.md"
          >
            <Input placeholder="runbooks/deploy.md" />
          </Form.Item>
          <Form.Item name="title" label="Title" style={{ flex: 2, marginLeft: 8 }}>
            <Input placeholder="(from the path)" />
          </Form.Item>
        </Space.Compact>
        <Form.Item
          name="abstract"
          label="Abstract (always shown to agents)"
          extra="One line saying what this doc covers and when to read it. Left empty, the first line of the body is used."
        >
          <Input maxLength={300} showCount placeholder="How to deploy to staging and prod; read before any release task" />
        </Form.Item>
        <Form.Item name="body" label="Body (read by agents only when relevant)">
          <Input.TextArea autoSize={{ minRows: 8, maxRows: 22 }} style={{ fontFamily: 'monospace' }} placeholder="Markdown…" />
        </Form.Item>
        <Space size="large" align="start">
          <Form.Item name="scope" label="Scope">
            <Select
              style={{ width: 300 }}
              options={[
                ...(isAdmin ? [{ value: 'global', label: 'Global — every agent task' }] : []),
                { value: 'project', label: 'Project — overrides a global doc at the same path' },
              ]}
            />
          </Form.Item>
          {scope === 'project' && (
            <Form.Item name="project_id" label="Project" rules={[{ required: true, message: 'Pick a project' }]}>
              <Select
                style={{ width: 220 }}
                options={projects
                  .filter((p) => !p.archived && (isAdmin || p.my_role === 'maintainer'))
                  .map((p) => ({ value: p.id, label: p.name }))}
              />
            </Form.Item>
          )}
          <Form.Item name="enabled" label="Enabled" valuePropName="checked">
            <Switch />
          </Form.Item>
        </Space>
      </Form>
    </Modal>
  )
}

export default function ContextStoreView({ projects = [], isAdmin = false }) {
  const [docs, setDocs] = useState([])
  const [loading, setLoading] = useState(false)
  const [editing, setEditing] = useState(null) // null=closed, {}=new, doc=edit
  const [previewFor, setPreviewFor] = useState(undefined)
  const [preview, setPreview] = useState(null)

  const projName = (id) => projects.find((p) => p.id === id)?.name
  // Global docs: admins only. Project docs: maintainers of that project.
  const canWrite = (doc) =>
    isAdmin ||
    (doc.scope === 'project' && projects.find((p) => p.id === doc.project_id)?.my_role === 'maintainer')
  const canAdd = isAdmin || projects.some((p) => !p.archived && p.my_role === 'maintainer')

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      setDocs(await api.listContextDocs())
    } catch (e) {
      message.error(e.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  const openPreview = async (projectId) => {
    try {
      setPreview(await api.previewContext(projectId))
    } catch (e) {
      message.error(e.message)
    }
  }

  const toggle = async (row, enabled) => {
    try {
      await api.updateContextDoc(row.id, { enabled })
      refresh()
    } catch (e) {
      message.error(e.message)
    }
  }

  const remove = async (id) => {
    try {
      await api.deleteContextDoc(id)
      refresh()
    } catch (e) {
      message.error(e.message)
    }
  }

  const columns = [
    {
      title: 'Doc',
      dataIndex: 'path',
      render: (path, row) => (
        <div>
          <Typography.Text code>{path}</Typography.Text> <b>{row.title}</b>
          {row.abstract && <div style={{ fontSize: 12, color: '#8a7a6d' }}>{row.abstract}</div>}
        </div>
      ),
    },
    {
      title: 'Size',
      dataIndex: 'body',
      width: 90,
      render: (b) => <span style={{ fontSize: 12 }}>~{Math.ceil((b || '').length / 4)} tok</span>,
    },
    {
      title: 'Scope',
      dataIndex: 'scope',
      width: 150,
      render: (sc, row) =>
        sc === 'project' ? <Tag color="blue">{projName(row.project_id) || 'project'}</Tag> : <Tag>global</Tag>,
    },
    {
      title: 'Enabled',
      dataIndex: 'enabled',
      width: 90,
      render: (en, row) =>
        canWrite(row) ? (
          <Switch checked={en} onChange={(v) => toggle(row, v)} />
        ) : en ? (
          <Tag color="green">on</Tag>
        ) : (
          <Tag>off</Tag>
        ),
    },
    {
      title: '',
      key: 'a',
      width: 100,
      render: (_, row) =>
        canWrite(row) && (
          <Space>
            <Button size="small" icon={<EditOutlined />} onClick={() => setEditing(row)} />
            <Popconfirm title="Delete this doc?" onConfirm={() => remove(row.id)}>
              <Button size="small" danger icon={<DeleteOutlined />} />
            </Popconfirm>
          </Space>
        ),
    },
  ]

  return (
    <>
      <Space style={{ marginBottom: 12 }} wrap>
        {canAdd && (
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setEditing({})}>
            Add doc
          </Button>
        )}
        <Button icon={<ReloadOutlined />} onClick={refresh}>
          Refresh
        </Button>
        <Select
          allowClear
          style={{ width: 220 }}
          placeholder="Preview for: (no project)"
          value={previewFor}
          onChange={setPreviewFor}
          options={projects.filter((p) => !p.archived).map((p) => ({ value: p.id, label: p.name }))}
        />
        <Button icon={<EyeOutlined />} onClick={() => openPreview(previewFor)}>
          Preview what agents see
        </Button>
      </Space>
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 12 }}
        message="Curated knowledge for agents: runbooks, conventions, architecture, decisions."
        description="Every task gets the one-line abstracts (cheap). The full bodies are written to a read-only snapshot, and the agent opens only the ones it needs. Project memory still captures task history automatically; this is what you curate yourself."
      />

      <Table
        rowKey="id"
        size="middle"
        loading={loading}
        columns={columns}
        dataSource={docs}
        pagination={false}
        locale={{ emptyText: 'No knowledge docs yet. Add a runbook or conventions doc.' }}
      />

      <DocModal
        open={editing !== null}
        doc={editing && editing.id ? editing : null}
        projects={projects}
        isAdmin={isAdmin}
        onClose={() => setEditing(null)}
        onSaved={refresh}
      />

      <Drawer
        open={!!preview}
        width={640}
        title={`Injected index${previewFor ? `: ${projName(previewFor)}` : ' (global only)'}`}
        onClose={() => setPreview(null)}
      >
        {preview && (
          <>
            <Typography.Paragraph type="secondary">
              {preview.docs.length} doc(s) apply. ~{Math.ceil(preview.index.length / 4)} tokens are added to each task
              prompt; bodies are read only on demand.
            </Typography.Paragraph>
            <pre style={{ whiteSpace: 'pre-wrap', fontSize: 12 }}>{preview.index || '(nothing injected)'}</pre>
          </>
        )}
      </Drawer>
    </>
  )
}
