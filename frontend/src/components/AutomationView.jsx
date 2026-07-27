import { useCallback, useEffect, useState } from 'react'
import {
  Button,
  Card,
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
  Tabs,
  Tag,
  Typography,
  message,
} from 'antd'
import {
  ClockCircleOutlined,
  DeleteOutlined,
  EditOutlined,
  HistoryOutlined,
  PlusOutlined,
  ReloadOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons'
import dayjs from 'dayjs'
import { api, STATUS_COLORS } from '../api'

const MODEL_OPTS = [
  { value: 'sonnet', label: 'Sonnet' },
  { value: 'opus', label: 'Opus' },
  { value: 'haiku', label: 'Haiku' },
]

const CRON_PRESETS = [
  { value: '*/15 * * * *', label: 'Every 15 minutes' },
  { value: '0 * * * *', label: 'Hourly' },
  { value: '0 9 * * *', label: 'Daily at 9am' },
  { value: '0 9 * * 1-5', label: 'Weekdays at 9am' },
  { value: '0 9 * * 1', label: 'Mondays at 9am' },
  { value: '0 0 1 * *', label: 'Monthly (1st, midnight)' },
]

function ScheduleModal({ open, schedule, projects, onClose, onSaved }) {
  const [form] = Form.useForm()
  const [saving, setSaving] = useState(false)
  const [cron, setCron] = useState('0 9 * * 1-5')
  const [preview, setPreview] = useState([])
  const isEdit = !!schedule

  useEffect(() => {
    if (!open) return
    if (schedule) {
      setCron(schedule.cron)
      form.setFieldsValue({ ...schedule, model: schedule.model || undefined })
    } else {
      form.resetFields()
      setCron('0 9 * * 1-5')
      form.setFieldsValue({
        cron: '0 9 * * 1-5',
        priority: 'normal',
        max_turns: 25,
        enabled: true,
        notify: 'never',
      })
    }
  }, [open, schedule])

  useEffect(() => {
    if (!open || !cron) return
    let stop = false
    api
      .previewCron(cron)
      .then((r) => !stop && setPreview(r.next_runs))
      .catch(() => !stop && setPreview([]))
    return () => {
      stop = true
    }
  }, [cron, open])

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
      if (isEdit) await api.updateSchedule(schedule.id, payload)
      else await api.createSchedule(payload)
      message.success(isEdit ? 'Schedule saved' : 'Schedule created')
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
      title={isEdit ? `Edit ${schedule.name}` : 'New Schedule'}
      open={open}
      onCancel={onClose}
      onOk={submit}
      confirmLoading={saving}
      width={620}
    >
      <Form form={form} layout="vertical">
        <Form.Item name="name" label="Name" rules={[{ required: true }]}>
          <Input placeholder="e.g. Daily PR digest" />
        </Form.Item>

        <Form.Item label="Schedule">
          <Space.Compact style={{ width: '100%' }}>
            <Select
              style={{ width: 220 }}
              value={CRON_PRESETS.some((p) => p.value === cron) ? cron : undefined}
              placeholder="Common presets"
              options={CRON_PRESETS}
              onChange={(v) => {
                setCron(v)
                form.setFieldValue('cron', v)
              }}
            />
            <Form.Item name="cron" noStyle rules={[{ required: true }]}>
              <Input
                placeholder="raw cron e.g. 0 9 * * 1-5"
                onChange={(e) => setCron(e.target.value)}
              />
            </Form.Item>
          </Space.Compact>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {preview.length
              ? `Next: ${preview.slice(0, 3).map((d) => dayjs(d).format('MMM D HH:mm')).join(' · ')}`
              : 'Enter a valid cron to preview run times'}
          </Typography.Text>
        </Form.Item>

        <Form.Item name="prompt" label="Prompt" rules={[{ required: true }]}>
          <Input.TextArea rows={3} placeholder="What should the scheduled task do?" />
        </Form.Item>

        <div style={{ display: 'flex', gap: 12 }}>
          <Form.Item name="project_id" label="Project" style={{ flex: 1 }}>
            <Select
              allowClear
              placeholder="No project"
              options={projects.map((p) => ({ value: p.id, label: p.name }))}
            />
          </Form.Item>
          <Form.Item name="model" label="Model" style={{ width: 140 }}>
            <Select allowClear placeholder="Default" options={MODEL_OPTS} />
          </Form.Item>
          <Form.Item name="priority" label="Priority" style={{ width: 130 }}>
            <Select
              options={['low', 'normal', 'high', 'urgent'].map((p) => ({ value: p, label: p }))}
            />
          </Form.Item>
        </div>

        <div style={{ display: 'flex', gap: 12 }}>
          <Form.Item name="notify" label="Notify (Slack)" style={{ width: 180 }}>
            <Select
              options={[
                { value: 'never', label: 'Never' },
                { value: 'on_failure', label: 'On failure' },
                { value: 'always', label: 'Always' },
              ]}
            />
          </Form.Item>
          <Form.Item name="notify_webhook" label="Slack webhook URL" style={{ flex: 1 }}>
            <Input placeholder="https://hooks.slack.com/services/…" />
          </Form.Item>
          <Form.Item name="enabled" label="Enabled" valuePropName="checked">
            <Switch />
          </Form.Item>
        </div>
      </Form>
    </Modal>
  )
}

function RunsDrawer({ schedule, onClose }) {
  const [runs, setRuns] = useState([])
  useEffect(() => {
    if (schedule) api.scheduleRuns(schedule.id).then(setRuns)
  }, [schedule])
  return (
    <Drawer
      open={!!schedule}
      onClose={onClose}
      width={520}
      title={schedule ? `Run history — ${schedule.name}` : ''}
    >
      {runs.length ? (
        <List
          size="small"
          dataSource={runs}
          renderItem={(t) => (
            <List.Item>
              <List.Item.Meta
                title={<Tag color={STATUS_COLORS[t.status]}>{t.status}</Tag>}
                description={t.title}
              />
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                {dayjs(t.created_at).format('MMM D HH:mm')}
              </Typography.Text>
            </List.Item>
          )}
        />
      ) : (
        <Empty description="No runs yet" />
      )}
    </Drawer>
  )
}

function SchedulesTab({ projects }) {
  const [schedules, setSchedules] = useState([])
  const [loading, setLoading] = useState(false)
  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState(null)
  const [runsFor, setRunsFor] = useState(null)

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      setSchedules(await api.listSchedules())
    } catch (e) {
      message.error(e.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  const toggle = async (row, enabled) => {
    try {
      await api.updateSchedule(row.id, { enabled })
      refresh()
    } catch (e) {
      message.error(e.message)
    }
  }
  const act = async (fn, id, msg) => {
    try {
      await fn(id)
      message.success(msg)
      refresh()
    } catch (e) {
      message.error(e.message)
    }
  }

  const columns = [
    { title: 'Name', dataIndex: 'name', render: (n) => <b>{n}</b> },
    { title: 'Cron', dataIndex: 'cron', render: (c) => <code>{c}</code> },
    {
      title: 'Next run',
      dataIndex: 'next_run_at',
      render: (d) => (d ? dayjs(d).format('MMM D HH:mm') : '—'),
    },
    {
      title: 'Last run',
      dataIndex: 'last_run_at',
      render: (d) => (d ? dayjs(d).fromNow() : 'never'),
    },
    {
      title: 'Notify',
      dataIndex: 'notify',
      width: 90,
      render: (n) => (n === 'never' ? '—' : <Tag>{n}</Tag>),
    },
    {
      title: 'Enabled',
      dataIndex: 'enabled',
      width: 80,
      render: (e, row) => <Switch size="small" checked={e} onChange={(v) => toggle(row, v)} />,
    },
    {
      title: 'Actions',
      key: 'a',
      width: 220,
      render: (_, row) => (
        <Space size="small">
          <Button
            size="small"
            icon={<ThunderboltOutlined />}
            onClick={() => act(api.runSchedule, row.id, 'Run started')}
          >
            Run
          </Button>
          <Button size="small" icon={<HistoryOutlined />} onClick={() => setRunsFor(row)} />
          <Button
            size="small"
            icon={<EditOutlined />}
            onClick={() => {
              setEditing(row)
              setModalOpen(true)
            }}
          />
          <Popconfirm title="Delete schedule?" onConfirm={() => act(api.deleteSchedule, row.id, 'Deleted')}>
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
          New Schedule
        </Button>
        <Button icon={<ReloadOutlined />} onClick={refresh}>
          Refresh
        </Button>
      </Space>
      <Table
        rowKey="id"
        size="middle"
        loading={loading}
        columns={columns}
        dataSource={schedules}
        pagination={false}
        locale={{ emptyText: <Empty description="No schedules yet" /> }}
      />
      <ScheduleModal
        open={modalOpen}
        schedule={editing}
        projects={projects}
        onClose={() => setModalOpen(false)}
        onSaved={refresh}
      />
      <RunsDrawer schedule={runsFor} onClose={() => setRunsFor(null)} />
    </>
  )
}

function TemplatesTab({ projects }) {
  const [templates, setTemplates] = useState([])
  const [presets, setPresets] = useState([])
  const [modalOpen, setModalOpen] = useState(false)
  const [form] = Form.useForm()

  const refresh = useCallback(async () => {
    try {
      const [t, p] = await Promise.all([api.listTemplates(), api.templatePresets()])
      setTemplates(t)
      setPresets(p)
    } catch (e) {
      message.error(e.message)
    }
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  const addPreset = async (p) => {
    try {
      await api.createTemplate({
        name: p.name,
        description: p.description,
        prompt: p.prompt,
        tags: p.tags || [],
      })
      message.success(`Added template “${p.name}”`)
      refresh()
    } catch (e) {
      message.error(e.message)
    }
  }
  const act = async (fn, id, msg) => {
    try {
      await fn(id)
      message.success(msg)
      refresh()
    } catch (e) {
      message.error(e.message)
    }
  }
  const create = async () => {
    let v
    try {
      v = await form.validateFields()
    } catch {
      return
    }
    try {
      await api.createTemplate({ ...v, model: v.model || undefined })
      message.success('Template created')
      form.resetFields()
      setModalOpen(false)
      refresh()
    } catch (e) {
      message.error(e.message)
    }
  }

  return (
    <>
      <Typography.Title level={5}>Pre-built automations</Typography.Title>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12, marginBottom: 20 }}>
        {presets.map((p) => (
          <Card key={p.name} size="small" style={{ width: 260 }} title={p.name}>
            <Typography.Paragraph type="secondary" style={{ fontSize: 12, minHeight: 40 }}>
              {p.description}
            </Typography.Paragraph>
            <Button size="small" icon={<PlusOutlined />} onClick={() => addPreset(p)}>
              Add as template
            </Button>
          </Card>
        ))}
      </div>

      <Space style={{ marginBottom: 12 }}>
        <Typography.Title level={5} style={{ margin: 0 }}>
          Saved templates
        </Typography.Title>
        <Button size="small" icon={<PlusOutlined />} onClick={() => setModalOpen(true)}>
          New Template
        </Button>
      </Space>
      <Table
        rowKey="id"
        size="small"
        dataSource={templates}
        pagination={false}
        locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="No templates" /> }}
        columns={[
          { title: 'Name', dataIndex: 'name', render: (n) => <b>{n}</b> },
          { title: 'Prompt', dataIndex: 'prompt', ellipsis: true },
          { title: 'Model', dataIndex: 'model', width: 90, render: (m) => m || 'default' },
          {
            title: 'Actions',
            key: 'a',
            width: 150,
            render: (_, row) => (
              <Space size="small">
                <Button
                  size="small"
                  icon={<ThunderboltOutlined />}
                  onClick={() => act(api.runTemplate, row.id, 'Task started')}
                >
                  Run
                </Button>
                <Popconfirm title="Delete template?" onConfirm={() => act(api.deleteTemplate, row.id, 'Deleted')}>
                  <Button size="small" danger icon={<DeleteOutlined />} />
                </Popconfirm>
              </Space>
            ),
          },
        ]}
      />

      <Modal
        title="New Template"
        open={modalOpen}
        onCancel={() => setModalOpen(false)}
        onOk={create}
        width={560}
      >
        <Form form={form} layout="vertical" initialValues={{ priority: 'normal', max_turns: 25 }}>
          <Form.Item name="name" label="Name" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="prompt" label="Prompt" rules={[{ required: true }]}>
            <Input.TextArea rows={3} />
          </Form.Item>
          <div style={{ display: 'flex', gap: 12 }}>
            <Form.Item name="project_id" label="Project" style={{ flex: 1 }}>
              <Select
                allowClear
                placeholder="No project"
                options={projects.map((p) => ({ value: p.id, label: p.name }))}
              />
            </Form.Item>
            <Form.Item name="model" label="Model" style={{ width: 130 }}>
              <Select allowClear placeholder="Default" options={MODEL_OPTS} />
            </Form.Item>
          </div>
        </Form>
      </Modal>
    </>
  )
}

export default function AutomationView({ projects = [] }) {
  return (
    <Tabs
      items={[
        {
          key: 'schedules',
          label: (
            <span>
              <ClockCircleOutlined /> Schedules
            </span>
          ),
          children: <SchedulesTab projects={projects} />,
        },
        {
          key: 'templates',
          label: 'Templates',
          children: <TemplatesTab projects={projects} />,
        },
      ]}
    />
  )
}
