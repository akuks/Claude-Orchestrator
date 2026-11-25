import { useCallback, useEffect, useState } from 'react'
import { Button, Form, Input, Modal, Popconfirm, Select, Space, Switch, Table, Tag, Typography, message } from 'antd'
import { KeyOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons'
import { when } from '../time'
import { api } from '../api'
import { userTag } from '../plan/plan'
import OrgFallbackCard from './OrgFallbackCard'

const ROLES = [
  { value: 'admin', label: 'admin' },
  { value: 'member', label: 'member' },
]

function CreateModal({ open, onClose, onDone }) {
  const [form] = Form.useForm()
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    if (open) form.setFieldsValue({ email: '', name: '', role: 'member', password: '' })
  }, [open])
  const submit = async () => {
    const v = await form.validateFields().catch(() => null)
    if (!v) return
    setBusy(true)
    try {
      await api.createUser(v)
      message.success('User created')
      onDone?.()
      onClose()
    } catch (e) {
      message.error(e.message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <Modal title="Add user" open={open} onCancel={onClose} onOk={submit} confirmLoading={busy} okText="Create" destroyOnHidden>
      <Form form={form} layout="vertical">
        <Form.Item name="name" label="Name" rules={[{ required: true }]}>
          <Input />
        </Form.Item>
        <Form.Item name="email" label="Email" rules={[{ required: true, type: 'email' }]}>
          <Input />
        </Form.Item>
        <Form.Item name="role" label="Role" rules={[{ required: true }]}>
          <Select options={ROLES} />
        </Form.Item>
        <Form.Item name="password" label="Initial password" rules={[{ required: true, min: 8, message: 'At least 8 characters' }]}>
          <Input.Password autoComplete="new-password" />
        </Form.Item>
      </Form>
    </Modal>
  )
}

function ResetModal({ user, onClose }) {
  const [form] = Form.useForm()
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    if (user) form.resetFields()
  }, [user])
  const submit = async () => {
    const v = await form.validateFields().catch(() => null)
    if (!v) return
    setBusy(true)
    try {
      await api.resetUserPassword(user.id, v.password)
      message.success('Password reset')
      onClose()
    } catch (e) {
      message.error(e.message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <Modal title={`Reset password — ${user?.email || ''}`} open={!!user} onCancel={onClose} onOk={submit} confirmLoading={busy} okText="Reset" destroyOnHidden>
      <Form form={form} layout="vertical">
        <Form.Item name="password" label="New password" rules={[{ required: true, min: 8, message: 'At least 8 characters' }]}>
          <Input.Password autoComplete="new-password" />
        </Form.Item>
      </Form>
    </Modal>
  )
}

export default function UsersView({ currentUser, onFallbackChanged }) {
  const [users, setUsers] = useState([])
  const [loading, setLoading] = useState(false)
  const [createOpen, setCreateOpen] = useState(false)
  const [resetFor, setResetFor] = useState(null)
  const [plans, setPlans] = useState([])
  const loadPlans = useCallback(() => {
    api.planStatusAll().then((r) => setPlans(Array.isArray(r) ? r : [])).catch(() => setPlans([]))
  }, [])
  useEffect(() => {
    loadPlans()
  }, [loadPlans])
  const planOf = (owner) => plans.find((p) => String(p.credential_owner) === String(owner))
  const tryNow = async (owner) => {
    try {
      await api.planTryNow(owner)
      message.success('Work can start now.')
    } catch (e) {
      message.error(e.message)
    }
    loadPlans()
  }
  const tryNowButton = (owner, name) => (
    <Popconfirm
      placement="left"
      title={`Let ${name}'s work start now? If the limit is still reached, it will pause again.`}
      onConfirm={() => tryNow(owner)}
    >
      <Button size="small">Try now</Button>
    </Popconfirm>
  )
  const planCell = (owner, name) => {
    const tag = userTag(planOf(owner))
    return tag ? (
      <Space size="small">
        <Tag color={tag.color}>{tag.text}</Tag>
        {tag.color === 'red' && tryNowButton(owner, name)}
      </Space>
    ) : null
  }
  const org = planOf('org')
  const orgRow = org ? [{ id: 'org', name: 'Org fallback key', email: '', isOrg: true }] : []

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setUsers(await api.listUsers())
    } catch (e) {
      message.error(e.message)
    } finally {
      setLoading(false)
    }
  }, [])
  useEffect(() => {
    load()
  }, [load])

  const patch = async (u, payload) => {
    try {
      await api.updateUser(u.id, payload)
      load()
    } catch (e) {
      message.error(e.message)
      load()
    }
  }

  const columns = [
    { title: 'Name', dataIndex: 'name' },
    { title: 'Email', dataIndex: 'email' },
    {
      title: 'Claude plan',
      key: 'plan',
      render: (_, u) => planCell(u.isOrg ? 'org' : u.id, u.name),
    },
    {
      title: 'Role',
      dataIndex: 'role',
      width: 130,
      render: (r, u) => u.isOrg ? null : (
        <Select size="small" value={r} options={ROLES} style={{ width: 100 }} onChange={(v) => patch(u, { role: v })} />
      ),
    },
    {
      title: 'Active',
      dataIndex: 'active',
      width: 90,
      render: (a, u) => {
        if (u.isOrg) return null
        const sw = <Switch size="small" checked={a} disabled={u.id === currentUser?.id} onChange={(v) => patch(u, { active: v })} />
        return a && u.id !== currentUser?.id ? (
          <Popconfirm title="Deactivate this user?" onConfirm={() => patch(u, { active: false })}>
            <Switch size="small" checked={a} />
          </Popconfirm>
        ) : (
          sw
        )
      },
    },
    {
      title: 'Last login',
      dataIndex: 'last_login_at',
      width: 140,
      render: (d, u) => u.isOrg ? null : (d ? when(d).fromNow() : <Typography.Text type="secondary">never</Typography.Text>),
    },
    {
      title: '',
      key: 'actions',
      width: 150,
      render: (_, u) => u.isOrg ? null : (
        <Space>
          {u.id === currentUser?.id && <Tag>you</Tag>}
          <Button size="small" icon={<KeyOutlined />} onClick={() => setResetFor(u)}>
            Reset password
          </Button>
        </Space>
      ),
    },
  ]

  return (
    <>
      <OrgFallbackCard onChanged={onFallbackChanged} />
      <Space style={{ marginBottom: 12 }}>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
          Add user
        </Button>
        <Button icon={<ReloadOutlined />} onClick={load}>
          Refresh
        </Button>
      </Space>
      <Table rowKey="id" size="middle" loading={loading} columns={columns} dataSource={[...orgRow, ...users]} pagination={false} />
      <CreateModal open={createOpen} onClose={() => setCreateOpen(false)} onDone={load} />
      <ResetModal user={resetFor} onClose={() => setResetFor(null)} />
    </>
  )
}
