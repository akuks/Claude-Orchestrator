import { useCallback, useEffect, useState } from 'react'
import {
  Alert,
  Button,
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
import { DeleteOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons'
import { api } from '../api'

function InstallModal({ open, projects, onClose, onDone }) {
  const [form] = Form.useForm()
  const [installing, setInstalling] = useState(false)

  useEffect(() => {
    if (open) form.setFieldsValue({ scope: 'global', source_url: '', project_id: undefined })
  }, [open])

  const submit = async () => {
    const v = await form.validateFields().catch(() => null)
    if (!v) return
    setInstalling(true)
    try {
      await api.installSkill({ source_url: v.source_url, scope: v.scope, project_id: v.project_id || undefined })
      message.success('Skill pack installed')
      onDone?.()
      onClose()
    } catch (e) {
      message.error(e.message)
    } finally {
      setInstalling(false)
    }
  }

  return (
    <Modal open={open} title="Install a skill pack" onCancel={onClose} onOk={submit} confirmLoading={installing} okText="Install">
      <Alert
        type="warning"
        showIcon
        style={{ marginBottom: 12 }}
        message="A skill pack is a Claude Code plugin cloned from a git repo. Its skills (and any helper scripts an agent runs) execute with the agent — only install packs you trust."
      />
      <Form form={form} layout="vertical">
        <Form.Item name="source_url" label="Git repo URL" rules={[{ required: true, message: 'Paste a git repo URL' }]}>
          <Input placeholder="https://github.com/owner/repo" />
        </Form.Item>
        <Form.Item name="scope" label="Scope">
          <Select
            options={[
              { value: 'global', label: 'Global — available to every agent task' },
              { value: 'project', label: 'Project — only this project’s tasks' },
            ]}
          />
        </Form.Item>
        <Form.Item
          name="project_id"
          label="Project"
          shouldUpdate
          dependencies={['scope']}
          hidden={false}
        >
          <Select
            allowClear
            placeholder="(for project scope)"
            options={projects.filter((p) => !p.archived).map((p) => ({ value: p.id, label: p.name }))}
          />
        </Form.Item>
      </Form>
    </Modal>
  )
}

export default function SkillsView({ projects = [] }) {
  const [packs, setPacks] = useState([])
  const [presets, setPresets] = useState([])
  const [loading, setLoading] = useState(false)
  const [installing, setInstalling] = useState(null)
  const [modalOpen, setModalOpen] = useState(false)

  const projName = (id) => projects.find((p) => p.id === id)?.name

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const [p, pr] = await Promise.all([api.listSkills(), api.skillPresets()])
      setPacks(p)
      setPresets(pr)
    } catch (e) {
      message.error(e.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  const installPreset = async (pr) => {
    setInstalling(pr.name)
    try {
      await api.installSkill({ source_url: pr.source_url, description: pr.description, scope: 'global' })
      message.success(`Installed “${pr.name}”`)
      refresh()
    } catch (e) {
      message.error(e.message)
    } finally {
      setInstalling(null)
    }
  }

  const toggle = async (row, enabled) => {
    try {
      await api.updateSkill(row.id, { enabled })
      refresh()
    } catch (e) {
      message.error(e.message)
    }
  }

  const remove = async (id) => {
    try {
      await api.deleteSkill(id)
      refresh()
    } catch (e) {
      message.error(e.message)
    }
  }

  const installedUrls = new Set(packs.map((p) => p.source_url).filter(Boolean))

  const columns = [
    {
      title: 'Skill pack',
      dataIndex: 'name',
      render: (n, row) => (
        <div>
          <b>{n}</b> {row.version && <Tag>{`v${row.version}`}</Tag>}
          {row.description && <div style={{ fontSize: 12, color: '#8a7a6d' }}>{row.description}</div>}
        </div>
      ),
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
      render: (en, row) => <Switch checked={en} onChange={(v) => toggle(row, v)} />,
    },
    {
      title: '',
      key: 'a',
      width: 60,
      render: (_, row) => (
        <Popconfirm title="Remove this skill pack?" onConfirm={() => remove(row.id)}>
          <Button size="small" danger icon={<DeleteOutlined />} />
        </Popconfirm>
      ),
    },
  ]

  return (
    <>
      <Space style={{ marginBottom: 12 }} wrap>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setModalOpen(true)}>
          Install from git
        </Button>
        <Button icon={<ReloadOutlined />} onClick={refresh}>
          Refresh
        </Button>
        <Typography.Text type="secondary">
          Skill packs are Claude Code plugins loaded into agent tasks via --plugin-dir. Enable a pack to give agents its skills.
        </Typography.Text>
      </Space>

      {presets.length > 0 && (
        <Space wrap style={{ marginBottom: 12 }}>
          <Typography.Text type="secondary">Curated:</Typography.Text>
          {presets.map((pr) => (
            <Button
              key={pr.name}
              size="small"
              icon={<PlusOutlined />}
              loading={installing === pr.name}
              disabled={installedUrls.has(pr.source_url)}
              onClick={() => installPreset(pr)}
              title={pr.description}
            >
              {pr.name}
              {installedUrls.has(pr.source_url) && <Tag color="success" style={{ marginLeft: 6, marginRight: 0 }}>installed</Tag>}
            </Button>
          ))}
        </Space>
      )}

      <Table rowKey="id" size="middle" loading={loading} columns={columns} dataSource={packs} pagination={false}
        locale={{ emptyText: 'No skill packs yet — install one from the curated list or a git repo.' }} />

      <InstallModal open={modalOpen} projects={projects} onClose={() => setModalOpen(false)} onDone={refresh} />
    </>
  )
}
