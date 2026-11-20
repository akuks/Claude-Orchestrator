import { useCallback, useEffect, useMemo, useState } from 'react'
import { Button, Popconfirm, Select, Space, Table, Tag, Typography, message } from 'antd'
import { DeleteOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons'
import { api } from '../api'

const ROLE_OPTS = [
  { value: 'maintainer', label: 'maintainer' },
  { value: 'contributor', label: 'contributor' },
]
const key = (m) => `${m.subject_type}:${m.subject_id}`
const strip = (list) => list.map(({ subject_type, subject_id, role }) => ({ subject_type, subject_id, role }))

// Members of one project: users and teams with a role. Maintainers (incl.
// admins) edit a local draft and save it with one PUT; contributors read only.
export default function ProjectMembers({ project, currentUser, onSaved }) {
  const canEdit = project.my_role === 'maintainer'
  const isAdmin = currentUser?.role === 'admin'
  const [saved, setSaved] = useState([])
  const [draft, setDraft] = useState([])
  const [users, setUsers] = useState([])
  const [teams, setTeams] = useState([])
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [pick, setPick] = useState(null) // "user:<id>" | "team:<id>"
  const [pickRole, setPickRole] = useState('contributor')

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [m, u, t] = await Promise.all([
        api.getProjectMembers(project.id),
        canEdit ? api.userDirectory() : Promise.resolve([]),
        canEdit ? api.listTeams() : Promise.resolve([]),
      ])
      setSaved(m)
      setDraft(m)
      setUsers(u)
      setTeams(t)
    } catch (e) {
      message.error(e.message)
    } finally {
      setLoading(false)
    }
  }, [project.id, canEdit])
  useEffect(() => {
    load()
  }, [load])

  const dirty = JSON.stringify(strip(draft)) !== JSON.stringify(strip(saved))
  const taken = new Set(draft.map(key))

  const pickOptions = [
    {
      label: 'Users',
      options: users
        .filter((u) => !taken.has(`user:${u.id}`))
        .map((u) => ({ value: `user:${u.id}`, label: u.name ? `${u.name} (${u.email})` : u.email })),
    },
    {
      label: 'Teams',
      options: teams
        .filter((t) => !taken.has(`team:${t.id}`))
        .map((t) => ({ value: `team:${t.id}`, label: t.name })),
    },
  ].filter((g) => g.options.length)

  const add = () => {
    if (!pick) return
    const [subject_type, subject_id] = pick.split(':')
    const label =
      subject_type === 'user'
        ? users.find((u) => u.id === subject_id)?.name || subject_id
        : teams.find((t) => t.id === subject_id)?.name || subject_id
    setDraft((d) => [...d, { subject_type, subject_id, role: pickRole, label }])
    setPick(null)
  }
  const setRole = (m, role) => setDraft((d) => d.map((x) => (key(x) === key(m) ? { ...x, role } : x)))
  const remove = (m) => setDraft((d) => d.filter((x) => key(x) !== key(m)))

  // Would the current (non-admin) user still be a maintainer after saving? A
  // maintainer grant can come directly or through one of their teams.
  const keepsMaintainer = useMemo(() => {
    if (isAdmin || !currentUser) return true
    const myTeams = new Set(teams.filter((t) => t.members.some((u) => u.id === currentUser.id)).map((t) => t.id))
    return draft.some(
      (m) =>
        m.role === 'maintainer' &&
        ((m.subject_type === 'user' && m.subject_id === currentUser.id) ||
          (m.subject_type === 'team' && myTeams.has(m.subject_id)))
    )
  }, [draft, teams, currentUser, isAdmin])

  const save = async () => {
    setSaving(true)
    try {
      const out = await api.setProjectMembers(project.id, strip(draft))
      setSaved(out)
      setDraft(out)
      message.success('Members saved')
      onSaved?.() // my_role (or visibility) may have changed
    } catch (e) {
      message.error(e.message)
    } finally {
      setSaving(false)
    }
  }

  const columns = [
    {
      title: 'Member',
      dataIndex: 'label',
      render: (l, m) => (
        <Space>
          <b>{l}</b>
          {m.subject_type === 'user' && m.subject_id === currentUser?.id && <Tag>you</Tag>}
        </Space>
      ),
    },
    {
      title: 'Type',
      dataIndex: 'subject_type',
      width: 90,
      render: (t) => <Tag color={t === 'team' ? 'purple' : 'blue'}>{t}</Tag>,
    },
    {
      title: 'Role',
      dataIndex: 'role',
      width: 160,
      render: (r, m) =>
        canEdit ? (
          <Select size="small" style={{ width: 130 }} value={r} options={ROLE_OPTS} onChange={(v) => setRole(m, v)} />
        ) : (
          <Tag color={r === 'maintainer' ? 'gold' : 'default'}>{r}</Tag>
        ),
    },
    ...(canEdit
      ? [
          {
            title: '',
            key: 'a',
            width: 50,
            render: (_, m) => (
              <Button size="small" danger type="text" icon={<DeleteOutlined />} title="Remove" onClick={() => remove(m)} />
            ),
          },
        ]
      : []),
  ]

  const saveBtn = (
    <Button type="primary" loading={saving} disabled={!dirty} onClick={keepsMaintainer ? save : undefined}>
      Save members
    </Button>
  )

  return (
    <>
      {canEdit && (
        <Space style={{ marginBottom: 12 }} wrap>
          <Select
            showSearch
            style={{ width: 280 }}
            placeholder="Add a user or team"
            optionFilterProp="label"
            value={pick}
            onChange={setPick}
            options={pickOptions}
          />
          <Select style={{ width: 130 }} value={pickRole} onChange={setPickRole} options={ROLE_OPTS} />
          <Button icon={<PlusOutlined />} disabled={!pick} onClick={add}>
            Add
          </Button>
        </Space>
      )}
      <Table
        rowKey={key}
        size="small"
        loading={loading}
        columns={columns}
        dataSource={draft}
        pagination={false}
        locale={{ emptyText: 'No members — only admins can see this project.' }}
      />
      <Space style={{ marginTop: 12 }}>
        {canEdit &&
          (keepsMaintainer || !dirty ? (
            saveBtn
          ) : (
            <Popconfirm title="You'll lose maintainer access to this project. Save anyway?" onConfirm={save}>
              {saveBtn}
            </Popconfirm>
          ))}
        {canEdit && dirty && <Button onClick={() => setDraft(saved)}>Discard changes</Button>}
        <Button
          icon={<ReloadOutlined />}
          onClick={load}
          disabled={dirty}
          title={dirty ? 'Save or discard your changes first' : undefined}
        >
          Reload
        </Button>
      </Space>
      {!canEdit && (
        <Typography.Paragraph type="secondary" style={{ marginTop: 12 }}>
          Only maintainers can change who has access to this project.
        </Typography.Paragraph>
      )}
    </>
  )
}
