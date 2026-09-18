import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Button,
  Form,
  Input,
  Modal,
  Popconfirm,
  Select,
  Space,
  Table,
  Tooltip,
  Typography,
  message,
} from 'antd'
import {
  DeleteOutlined,
  EditOutlined,
  PlusOutlined,
  ReloadOutlined,
} from '@ant-design/icons'
import { api } from '../api'

// Editor for a project's servers.yaml inventory, used by the Remote Ops agents.
// The whole list is saved at once (PUT replaces the file).

function ServerModal({ open, server, existingNames, onClose, onSave }) {
  const [form] = Form.useForm()
  const isEdit = !!server

  useEffect(() => {
    if (!open) return
    if (server) form.setFieldsValue(server)
    else form.resetFields()
  }, [open, server])

  // NOTE: onOk must NOT return a promise. When it does, antd's async-close flow
  // ignores the controlled `open={false}` and the modal stays stuck open. So we
  // validate, close synchronously (like Cancel), and let the parent persist.
  const submit = () => {
    form
      .validateFields()
      .then((v) => {
        onClose()
        onSave({ ...v, notes: v.notes || null })
      })
      .catch(() => {}) // validation errors stay shown; modal stays open
  }

  return (
    <Modal
      title={isEdit ? `Edit ${server.name}` : 'Add server'}
      open={open}
      onCancel={onClose}
      onOk={submit}
      okText="Done"
      width={560}
    >
      <Form form={form} layout="vertical">
        <Form.Item
          name="name"
          label="Name"
          extra="What you'll call it at run time, e.g. “diagnose prod-web”."
          rules={[
            { required: true, message: 'Give it a name' },
            {
              validator: (_, val) =>
                !val || isEdit || !existingNames.includes(val)
                  ? Promise.resolve()
                  : Promise.reject(new Error('A server with this name already exists')),
            },
          ]}
        >
          <Input placeholder="prod-web" />
        </Form.Item>
        <div style={{ display: 'flex', gap: 12 }}>
          <Form.Item name="host" label="Host" style={{ flex: 1 }} rules={[{ required: true }]}>
            <Input placeholder="1.2.3.4 or host.example.com" />
          </Form.Item>
          <Form.Item name="user" label="User" style={{ width: 160 }} rules={[{ required: true }]}>
            <Input placeholder="ubuntu" />
          </Form.Item>
        </div>
        <Form.Item
          name="pem"
          label="PEM key path (on this host)"
          extra="Path to the private key on the machine running the orchestrator (chmod 600). Not uploaded — only the path is stored."
          rules={[{ required: true, message: 'Path to the private key' }]}
        >
          <Input placeholder="~/keys/prod-web.pem" />
        </Form.Item>
        <Form.Item name="notes" label="Notes">
          <Input.TextArea rows={2} placeholder="stack, ports, log locations…" />
        </Form.Item>
      </Form>
    </Modal>
  )
}

export default function ServersView({ projects = [] }) {
  const usable = useMemo(() => projects.filter((p) => !p.archived), [projects])
  const [projectId, setProjectId] = useState(null)
  const [servers, setServers] = useState([])
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState(null)

  // Default to a project named "Infra" if present, else the first project.
  useEffect(() => {
    if (projectId || usable.length === 0) return
    const infra = usable.find((p) => p.name.toLowerCase() === 'infra')
    setProjectId(infra ? infra.id : usable[0].id)
  }, [usable, projectId])

  const refresh = useCallback(async () => {
    if (!projectId) return
    setLoading(true)
    try {
      setServers(await api.listServers(projectId))
    } catch (e) {
      message.error(e.message)
    } finally {
      setLoading(false)
    }
  }, [projectId])

  useEffect(() => {
    refresh()
  }, [refresh])

  // Persist the whole inventory immediately — every add/edit/delete writes
  // servers.yaml, so there's no separate "save" step to forget.
  const persist = async (next, successMsg) => {
    setSaving(true)
    try {
      setServers(await api.saveServers(projectId, next))
      message.success(successMsg)
    } catch (e) {
      message.error(e.message)
      refresh() // roll back to what's actually on disk
      throw e // let the caller (modal) know it failed so it stays open
    } finally {
      setSaving(false)
    }
  }

  const upsert = (row) => {
    const i = editing ? servers.findIndex((x) => x.name === editing.name) : -1
    const next = i >= 0 ? servers.map((x, j) => (j === i ? row : x)) : [...servers, row]
    return persist(next, `Server “${row.name}” saved`)
  }

  const remove = (name) =>
    persist(servers.filter((x) => x.name !== name), `Server “${name}” removed`).catch(() => {})

  const columns = [
    { title: 'Name', dataIndex: 'name', render: (n) => <b>{n}</b>, width: 140 },
    { title: 'Host', dataIndex: 'host', width: 170 },
    { title: 'User', dataIndex: 'user', width: 110 },
    {
      title: 'PEM path',
      dataIndex: 'pem',
      render: (p) => <Typography.Text code>{p}</Typography.Text>,
    },
    {
      title: 'Notes',
      dataIndex: 'notes',
      render: (n) =>
        n ? (
          <Tooltip title={n}>
            <span style={{ color: '#8a7a6d' }}>{n.split('\n')[0].slice(0, 40)}</span>
          </Tooltip>
        ) : (
          <span style={{ color: '#c4b8ad' }}>—</span>
        ),
    },
    {
      title: 'Actions',
      key: 'a',
      width: 100,
      render: (_, row) => (
        <Space size="small">
          <Button
            size="small"
            icon={<EditOutlined />}
            disabled={saving}
            onClick={() => {
              setEditing(row)
              setModalOpen(true)
            }}
          />
          <Popconfirm title={`Remove ${row.name}?`} onConfirm={() => remove(row.name)}>
            <Button size="small" danger icon={<DeleteOutlined />} disabled={saving} />
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <>
      <Space style={{ marginBottom: 12 }} wrap>
        <Typography.Text type="secondary">Project</Typography.Text>
        <Select
          style={{ width: 200 }}
          value={projectId}
          onChange={setProjectId}
          options={usable.map((p) => ({ value: p.id, label: p.name }))}
          placeholder="Pick a project"
        />
        <Button
          type="primary"
          icon={<PlusOutlined />}
          disabled={!projectId || saving}
          onClick={() => {
            setEditing(null)
            setModalOpen(true)
          }}
        >
          Add server
        </Button>
        <Button icon={<ReloadOutlined />} onClick={refresh} disabled={!projectId}>
          Reload
        </Button>
        <Typography.Text type="secondary">
          Changes save to the project's <code>servers.yaml</code> immediately — read by the
          Remote Ops agents. Enable “Allow remote login (SSH)” on an agent to let it connect.
        </Typography.Text>
      </Space>
      <Table
        rowKey="name"
        size="middle"
        loading={loading || saving}
        columns={columns}
        dataSource={servers}
        pagination={false}
        locale={{ emptyText: 'No servers yet — add one to build the inventory.' }}
      />
      {/* Mount only while open: setting modalOpen=false unmounts the modal
          outright, which is reliable even when a state change during save would
          otherwise leave an antd Modal's open={false} stuck on screen. */}
      {modalOpen && (
        <ServerModal
          open
          server={editing}
          existingNames={servers.map((x) => x.name)}
          onClose={() => setModalOpen(false)}
          onSave={upsert}
        />
      )}
    </>
  )
}
