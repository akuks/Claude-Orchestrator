import { useCallback, useEffect, useState } from 'react'
import {
  Button,
  Layout,
  Segmented,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import {
  ApiOutlined,
  FolderOutlined,
  PlusOutlined,
  ReloadOutlined,
  UnorderedListOutlined,
} from '@ant-design/icons'
import dayjs from 'dayjs'
import relativeTime from 'dayjs/plugin/relativeTime'
import { api, PRIORITY_COLORS, STATUS_COLORS } from './api'
import StatsBar from './components/StatsBar'
import CreateTaskModal from './components/CreateTaskModal'
import TaskDetail from './components/TaskDetail'
import McpRegistry from './components/McpRegistry'
import ProjectsView from './components/ProjectsView'

dayjs.extend(relativeTime)

const { Header, Content } = Layout

export default function App() {
  const [tasks, setTasks] = useState([])
  const [stats, setStats] = useState(null)
  const [loading, setLoading] = useState(false)
  const [createOpen, setCreateOpen] = useState(false)
  const [selected, setSelected] = useState(null)
  const [statusFilter, setStatusFilter] = useState()
  const [view, setView] = useState('tasks')
  const [projects, setProjects] = useState([])
  const [activeProjectId, setActiveProjectId] = useState(null)

  const loadProjects = useCallback(async () => {
    try {
      setProjects(await api.listProjects())
    } catch (e) {
      message.error(e.message)
    }
  }, [])

  useEffect(() => {
    loadProjects()
  }, [loadProjects])

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const [t, s] = await Promise.all([
        api.listTasks({ status: statusFilter, project_id: activeProjectId }),
        api.stats(),
      ])
      setTasks(t)
      setStats(s)
    } catch (e) {
      message.error(e.message)
    } finally {
      setLoading(false)
    }
  }, [statusFilter, activeProjectId])

  useEffect(() => {
    if (view !== 'tasks') return
    refresh()
    const id = setInterval(refresh, 3000) // poll for live feed updates
    return () => clearInterval(id)
  }, [refresh, view])

  const activeProject = projects.find((p) => p.id === activeProjectId) || null

  const columns = [
    {
      title: 'Title',
      dataIndex: 'title',
      render: (t, row) => (
        <Space>
          <a onClick={() => setSelected(row.id)}>{t}</a>
          {row.thread_count > 1 && (
            <Tag color="cyan" title={`${row.thread_count} steps in this thread`}>
              🧵 {row.thread_count}
            </Tag>
          )}
        </Space>
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
              { value: 'projects', label: 'Projects', icon: <FolderOutlined /> },
              { value: 'mcp', label: 'MCP Servers', icon: <ApiOutlined /> },
            ]}
          />
        </Space>
        <Space>
          {view === 'tasks' && (
            <Select
              allowClear
              placeholder="All projects"
              style={{ width: 200 }}
              value={activeProjectId}
              onChange={(v) => setActiveProjectId(v || null)}
              options={projects.map((p) => ({ value: p.id, label: p.name }))}
            />
          )}
          {view === 'tasks' && (
            <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
              New Task
            </Button>
          )}
        </Space>
      </Header>

      <Content style={{ padding: 24 }}>
        {view === 'mcp' && <McpRegistry />}
        {view === 'projects' && <ProjectsView onProjectsChanged={loadProjects} />}
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
        projects={projects}
        activeProjectId={activeProjectId}
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
