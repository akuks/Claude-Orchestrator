import { useCallback, useEffect, useState } from 'react'
import { Button, Form, Input, Modal, Popconfirm, Select, Space, Table, message } from 'antd'
import { DeleteOutlined, EditOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons'
import { api } from '../api'

const userLabel = (u) => (u.name ? `${u.name} (${u.email})` : u.email)

// Create (team = null) or rename (team = {id, name}).
function NameModal({ open, team, onClose, onDone }) {
  const [form] = Form.useForm()
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    if (open) form.setFieldsValue({ name: team?.name || '' })
  }, [open, team])
  const submit = async () => {
    const v = await form.validateFields().catch(() => null)
    if (!v) return
    setBusy(true)
    try {
      if (team) await api.updateTeam(team.id, v.name.trim())
      else await api.createTeam(v.name.trim())
      message.success(team ? 'Team renamed' : 'Team created')
      onDone?.()
      onClose()
    } catch (e) {
      message.error(e.message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <Modal
      title={team ? `Rename ${team.name}` : 'New team'}
      open={open}
      onCancel={onClose}
      onOk={submit}
      confirmLoading={busy}
      okText={team ? 'Save' : 'Create'}
      destroyOnHidden
    >
      <Form form={form} layout="vertical">
        <Form.Item name="name" label="Name" rules={[{ required: true, whitespace: true }]}>
          <Input maxLength={120} placeholder="e.g. Platform" />
        </Form.Item>
      </Form>
    </Modal>
  )
}

export default function TeamsView() {
  const [teams, setTeams] = useState([])
  const [directory, setDirectory] = useState([])
  const [loading, setLoading] = useState(false)
  const [modal, setModal] = useState(null) // null=closed, {}=new, team=rename

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [t, d] = await Promise.all([api.listTeams(), api.userDirectory()])
      setTeams(t)
      setDirectory(d)
    } catch (e) {
      message.error(e.message)
    } finally {
      setLoading(false)
    }
  }, [])
  useEffect(() => {
    load()
  }, [load])

  const setMembers = async (team, userIds) => {
    try {
      const updated = await api.setTeamMembers(team.id, userIds)
      setTeams((ts) => ts.map((t) => (t.id === team.id ? updated : t)))
    } catch (e) {
      message.error(e.message)
      load()
    }
  }

  const remove = async (team) => {
    try {
      await api.deleteTeam(team.id)
      message.success('Team deleted')
      load()
    } catch (e) {
      message.error(e.message)
    }
  }

  const columns = [
    { title: 'Team', dataIndex: 'name', width: 220, render: (n) => <b>{n}</b> },
    {
      title: 'Members',
      dataIndex: 'members',
      render: (members, team) => {
        // The directory lists active users only; keep current (maybe disabled)
        // members selectable so they show with a label and can be removed.
        const known = new Set(directory.map((u) => u.id))
        const options = [...directory, ...members.filter((m) => !known.has(m.id))].map((u) => ({
          value: u.id,
          label: userLabel(u),
        }))
        return (
          <Select
            mode="multiple"
            style={{ width: '100%' }}
            placeholder="Add members"
            optionFilterProp="label"
            value={members.map((m) => m.id)}
            options={options}
            onChange={(ids) => setMembers(team, ids)}
          />
        )
      },
    },
    {
      title: '',
      key: 'actions',
      width: 90,
      render: (_, team) => (
        <Space>
          <Button size="small" icon={<EditOutlined />} title="Rename" onClick={() => setModal(team)} />
          <Popconfirm
            title="Delete this team? Its project grants are removed too."
            onConfirm={() => remove(team)}
          >
            <Button size="small" danger icon={<DeleteOutlined />} title="Delete" />
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <>
      <Space style={{ marginBottom: 12 }}>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setModal({})}>
          New team
        </Button>
        <Button icon={<ReloadOutlined />} onClick={load}>
          Refresh
        </Button>
      </Space>
      <Table
        rowKey="id"
        size="middle"
        loading={loading}
        columns={columns}
        dataSource={teams}
        pagination={false}
        locale={{ emptyText: 'No teams yet — create one, then grant it access on a project’s Members tab.' }}
      />
      <NameModal
        open={modal !== null}
        team={modal && modal.id ? modal : null}
        onClose={() => setModal(null)}
        onDone={load}
      />
    </>
  )
}
