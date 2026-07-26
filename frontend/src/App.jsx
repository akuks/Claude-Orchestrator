import { useCallback, useEffect, useState } from 'react'
import {
  Button,
  Input,
  Layout,
  Segmented,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import { ApiOutlined, PlusOutlined, ReloadOutlined, UnorderedListOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import relativeTime from 'dayjs/plugin/relativeTime'
import { api, PRIORITY_COLORS, STATUS_COLORS } from './api'
import StatsBar from './components/StatsBar'
import CreateTaskModal from './components/CreateTaskModal'
import TaskDetail from './components/TaskDetail'
import McpRegistry from './components/McpRegistry'

dayjs.extend(relativeTime)

const { Header, Content } = Layout

export default function App() {
  const [tasks, setTasks] = useState([])
  const [stats, setStats] = useState(null)
  const [loading, setLoading] = useState(false)
  const [createOpen, setCreateOpen] = useState(false)
  const [selected, setSelected] = useState(null)
  const [statusFilter, setStatusFilter] = useState()
  const [projectFilter, setProjectFilter] = useState('')
  const [view, setView] = useState('tasks')

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const [t, s] = await Promise.all([
        api.listTasks({ status: statusFilter, project: projectFilter }),
        api.stats(),
      ])
      setTasks(t)
      setStats(s)
    } catch (e) {
      message.error(e.message)
    } finally {
      setLoading(false)
    }
  }, [statusFilter, projectFilter])

  useEffect(() => {
    if (view !== 'tasks') return
    refresh()
    const id = setInterval(refresh, 3000) // poll for live feed updates
    return () => clearInterval(id)
  }, [refresh, view])

  const columns = [
    {
      title: 'Title',
      dataIndex: 'title',
      render: (t, row) => (
        <a onClick={() => setSelected(row.id)}>{t}</a>
      ),
      ellipsis: true,
    },
    {
      title: 'Status',
      dataIndex: 'status',
      width: 140,
      render: (s) => <Tag color={STATUS_COLORS[s]}>{s}</Tag>,
    },
    {
      title: 'Priority',
      dataIndex: 'priority',
      width: 100,
      render: (p) => <Tag color={PRIORITY_COLORS[p]}>{p}</Tag>,
    },
    { title: 'Model', dataIndex: 'model', width: 90 },
    { title: 'Project', dataIndex: 'project', width: 120, render: (p) => p || '—' },
    {
      title: 'Turns',
      dataIndex: 'num_turns',
      width: 70,
      render: (n) => n ?? '—',
    },
    {
      title: 'Cost',
      dataIndex: 'total_cost_usd',
      width: 90,
      render: (c) => (c != null ? `$${c.toFixed?.(4) ?? c}` : '—'),
    },
    {
      title: 'Created',
      dataIndex: 'created_at',
      width: 130,
      render: (d) => dayjs(d).fromNow(),
    },
  ]

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Header
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          paddingInline: 24,
        }}
      >
        <Space size="large">
          <Typography.Title level={4} style={{ color: '#fff', margin: 0 }}>
            🪄 Claude Orchestrator
          </Typography.Title>
          <Segmented
            value={view}
            onChange={setView}
            options={[
              { value: 'tasks', label: 'Tasks', icon: <UnorderedListOutlined /> },
              { value: 'mcp', label: 'MCP Servers', icon: <ApiOutlined /> },
            ]}
          />
        </Space>
        {view === 'tasks' && (
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
            New Task
          </Button>
        )}
      </Header>

      <Content style={{ padding: 24 }}>
        {view === 'mcp' && <McpRegistry />}
        {view === 'tasks' && (
        <>
        <StatsBar stats={stats} />

        <Space style={{ marginBottom: 12 }} wrap>
          <Select
            allowClear
            placeholder="Filter status"
            style={{ width: 180 }}
            value={statusFilter}
            onChange={setStatusFilter}
            options={[
              'queued',
              'running',
              'awaiting_approval',
              'completed',
              'failed',
              'cancelled',
            ].map((s) => ({ value: s, label: s }))}
          />
          <Input.Search
            allowClear
            placeholder="Filter project"
            style={{ width: 200 }}
            onSearch={setProjectFilter}
            onChange={(e) => !e.target.value && setProjectFilter('')}
          />
          <Button icon={<ReloadOutlined />} onClick={refresh}>
            Refresh
          </Button>
        </Space>

        <Table
          rowKey="id"
          size="middle"
          loading={loading}
          columns={columns}
          dataSource={tasks}
          pagination={{ pageSize: 15, showSizeChanger: false }}
        />
        </>
        )}
      </Content>

      <CreateTaskModal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={refresh}
      />
      <TaskDetail
        taskId={selected}
        onClose={() => setSelected(null)}
        onChanged={refresh}
        onOpenTask={(id) => setSelected(id)}
      />
    </Layout>
  )
}
