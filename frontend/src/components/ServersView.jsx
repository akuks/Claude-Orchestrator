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

  const submit = async () => {
    const v = await form.validateFields().catch(() => null)
    if (!v) return
    onSave({ ...v, notes: v.notes || null })
    onClose()
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
  const [dirty, setDirty] = useState(false)
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
      setDirty(false)
    } catch (e) {
      message.error(e.message)
    } finally {
      setLoading(false)
    }
  }, [projectId])

  useEffect(() => {
    refresh()
  }, [refresh])

  const upsert = (row) => {
    setServers((prev) => {
      const i = editing ? prev.findIndex((x) => x.name === editing.name) : -1
      if (i >= 0) {
        const next = [...prev]
        next[i] = row
        return next
      }
      return [...prev, row]
    })
    setDirty(true)
  }

  const remove = (name) => {
    setServers((prev) => prev.filter((x) => x.name !== name))
    setDirty(true)
  }

  const save = async () => {
    setSaving(true)
    try {
      setServers(await api.saveServers(projectId, servers))
      setDirty(false)
      message.success('Inventory saved to servers.yaml')
    } catch (e) {
      message.error(e.message)
    } finally {
      setSaving(false)
    }
  }

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
            onClick={() => {
              setEditing(row)
              setModalOpen(true)
            }}
          />
          <Popconfirm title={`Remove ${row.name}?`} onConfirm={() => remove(row.name)}>
            <Button size="small" danger icon={<DeleteOutlined />} />
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
          disabled={!projectId}
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
        <Button type="primary" ghost onClick={save} loading={saving} disabled={!dirty}>
          {dirty ? 'Save changes' : 'Saved'}
        </Button>
        <Typography.Text type="secondary">
          Edits the project's <code>servers.yaml</code>, read by the Remote Ops agents. Enable
          “Allow remote login (SSH)” on an agent to let it connect.
        </Typography.Text>
      </Space>
      <Table
        rowKey="name"
        size="middle"
        loading={loading}
        columns={columns}
        dataSource={servers}
        pagination={false}
        locale={{ emptyText: 'No servers yet — add one to build the inventory.' }}
      />
      <ServerModal
        open={modalOpen}
        server={editing}
        existingNames={servers.map((x) => x.name)}
        onClose={() => setModalOpen(false)}
        onSave={upsert}
      />
    </>
  )
}
