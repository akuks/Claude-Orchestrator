import { useCallback, useEffect, useRef, useState } from 'react'
import {
  Badge,
  Button,
  Dropdown,
  Layout,
  Spin,
  Popconfirm,
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
  ClockCircleOutlined,
  DeleteOutlined,
  DollarOutlined,
  FolderOutlined,
  LockOutlined,
  LogoutOutlined,
  TeamOutlined,
  UserOutlined,
  PlusOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
  SafetyOutlined,
  UnorderedListOutlined,
} from '@ant-design/icons'
import dayjs from 'dayjs'
import relativeTime from 'dayjs/plugin/relativeTime'
import { api, setUnauthorizedHandler, PRIORITY_COLORS, STATUS_COLORS, shortModel } from './api'
import StatsBar from './components/StatsBar'
import CreateTaskModal from './components/CreateTaskModal'
import CodeReviewModal from './components/CodeReviewModal'
import TaskDetail from './components/TaskDetail'
import McpRegistry from './components/McpRegistry'
import ProjectsView from './components/ProjectsView'
import AutomationView from './components/AutomationView'
import ApprovalsView from './components/ApprovalsView'
import UsageView from './components/UsageView'
import SecurityView from './components/SecurityView'
import UsersView from './components/UsersView'
import TeamsView from './components/TeamsView'
import LoginPage from './auth/LoginPage'
import SetupPage from './auth/SetupPage'
import ChangePasswordModal from './auth/ChangePasswordModal'

dayjs.extend(relativeTime)

const { Header, Content } = Layout

const EXPIRED_MSG = 'Your session expired — please sign in again.'

export default function App() {
  // phase: loading | setup | login | app
  const [phase, setPhase] = useState('loading')
  const [user, setUser] = useState(null)
  const [notice, setNotice] = useState(null)
  const userRef = useRef(null)
  userRef.current = user

  useEffect(() => {
    setUnauthorizedHandler(() => {
      if (!userRef.current) return
      setUser(null)
      setNotice(EXPIRED_MSG)
      setPhase('login')
    })
    ;(async () => {
      try {
        const st = await api.setupStatus()
        if (st.needs_setup) return setPhase('setup')
        try {
          setUser(await api.me())
          setPhase('app')
        } catch (e) {
          setPhase('login')
        }
      } catch (e) {
        setPhase('login')
      }
    })()
  }, [])

  const signedIn = (u) => {
    setUser(u)
    setNotice(null)
    setPhase('app')
  }
  const logout = async () => {
    try {
      await api.logout() // 401 here just means already logged out
    } catch (_) {}
    setUser(null)
    setNotice(null)
    setPhase('login')
  }

  if (phase === 'loading')
    return (
      <Layout style={{ minHeight: '100vh' }}>
        <div style={{ display: 'flex', justifyContent: 'center', paddingTop: 160 }}>
          <Spin size="large" />
        </div>
      </Layout>
    )
  if (phase === 'setup')
    return (
      <Layout style={{ minHeight: '100vh' }}>
        <SetupPage onDone={signedIn} />
      </Layout>
    )
  if (phase === 'login')
    return (
      <Layout style={{ minHeight: '100vh' }}>
        <LoginPage notice={notice} onLoggedIn={signedIn} />
      </Layout>
    )
  return <AppInner user={user} onLogout={logout} />
}

function AppInner({ user, onLogout }) {
  const isAdmin = user.role === 'admin'
  const [pwOpen, setPwOpen] = useState(false)
  const [tasks, setTasks] = useState([])
  const [stats, setStats] = useState(null)
  const [loading, setLoading] = useState(false)
  const [createOpen, setCreateOpen] = useState(false)
  const [reviewOpen, setReviewOpen] = useState(false)
  const [selected, setSelected] = useState(null)
  const [statusFilter, setStatusFilter] = useState()
  const [view, setView] = useState('tasks')
  const [projects, setProjects] = useState([])
  const [activeProjectId, setActiveProjectId] = useState(null)
  const [approvalCount, setApprovalCount] = useState(0)
  const [claudeInfo, setClaudeInfo] = useState(null)

  useEffect(() => {
    api.claudeStatus().then(setClaudeInfo).catch(() => setClaudeInfo(null))
  }, [])

  const loadApprovalCount = useCallback(async () => {
    try {
      setApprovalCount((await api.listApprovals()).length)
    } catch (_) {}
  }, [])

  useEffect(() => {
    loadApprovalCount()
    const id = setInterval(loadApprovalCount, 5000)
    return () => clearInterval(id)
  }, [loadApprovalCount])

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

  const deleteTask = async (id) => {
    try {
      await api.deleteTask(id)
      message.success('Task deleted')
      refresh()
    } catch (e) {
      message.error(e.message)
    }
  }

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
    {
      title: 'Model',
      dataIndex: 'model',
      width: 120,
      render: (m, row) => {
        const actual = shortModel(row.model_used)
        if (actual) return actual
        return m || <Typography.Text type="secondary">default</Typography.Text>
      },
    },
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
    {
      title: '',
      key: 'actions',
      width: 50,
      render: (_, row) => {
        const active = ['queued', 'running', 'awaiting_approval'].includes(row.status)
        return (
          <Popconfirm
            title={
              row.thread_count > 1
                ? 'Delete this whole thread?'
                : 'Delete this task?'
            }
            disabled={active}
            onConfirm={() => deleteTask(row.id)}
          >
            <Button
              type="text"
              size="small"
              danger
              icon={<DeleteOutlined />}
              disabled={active}
              title={active ? 'Cancel before deleting' : 'Delete'}
            />
          </Popconfirm>
        )
      },
    },
  ]

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Header
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          flexWrap: 'wrap',
          height: 'auto',
          minHeight: 64,
          rowGap: 8,
          paddingInline: 24,
          paddingBlock: 8,
        }}
      >
        <Space size="large" wrap>
          <Typography.Title
            level={4}
            style={{ color: '#fff', margin: 0, whiteSpace: 'nowrap' }}
            title="Claude Orchestrator"
          >
            🪄 CO
          </Typography.Title>
          {claudeInfo &&
            (claudeInfo.found && claudeInfo.authenticated ? (
              <Tag color="green" title={`auth: ${claudeInfo.auth_method}`}>
                claude {claudeInfo.version?.split(' ')[0] || 'ok'}
              </Tag>
            ) : (
              <Tag color="red" title="Set ANTHROPIC_API_KEY or run 'claude auth login'">
                claude {!claudeInfo.found ? 'not found' : 'not authed'}
              </Tag>
            ))}
          <Segmented
            value={view}
            onChange={setView}
            options={[
              { value: 'tasks', label: 'Tasks', icon: <UnorderedListOutlined /> },
              { value: 'projects', label: 'Projects', icon: <FolderOutlined /> },
              { value: 'automation', label: 'Automation', icon: <ClockCircleOutlined /> },
              {
                value: 'approvals',
                icon: <SafetyOutlined />,
                label: (
                  <Badge count={approvalCount} size="small" offset={[8, -2]}>
                    <span>Approvals</span>
                  </Badge>
                ),
              },
              { value: 'security', label: 'Security', icon: <SafetyCertificateOutlined /> },
              { value: 'usage', label: 'Usage', icon: <DollarOutlined /> },
              { value: 'mcp', label: 'MCP Servers', icon: <ApiOutlined /> },
              ...(isAdmin
                ? [
                    { value: 'users', label: 'Users', icon: <UserOutlined /> },
                    { value: 'teams', label: 'Teams', icon: <TeamOutlined /> },
                  ]
                : []),
            ]}
          />
        </Space>
        <Space wrap>
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
            <Button
              icon={<SafetyCertificateOutlined />}
              onClick={() => setReviewOpen(true)}
            >
              Security Review
            </Button>
          )}
          {view === 'tasks' && (
            <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
              New Task
            </Button>
          )}
          <Dropdown
            trigger={['click']}
            menu={{
              items: [
                {
                  key: 'me',
                  disabled: true,
                  label: (
                    <span>
                      {user.name}{' '}
                      <Tag style={{ marginInlineEnd: 0 }} color={user.role === 'admin' ? 'gold' : 'default'}>
                        {user.role}
                      </Tag>
                    </span>
                  ),
                },
                { type: 'divider' },
                { key: 'pw', icon: <LockOutlined />, label: 'Change password', onClick: () => setPwOpen(true) },
                { key: 'out', icon: <LogoutOutlined />, label: 'Log out', onClick: onLogout },
              ],
            }}
          >
            <Button icon={<UserOutlined />} data-testid="user-menu" title={user.name} aria-label="User menu" />
          </Dropdown>
        </Space>
      </Header>

      <Content style={{ padding: 24 }}>
        {view === 'mcp' && <McpRegistry />}
        {view === 'users' && isAdmin && <UsersView currentUser={user} />}
        {view === 'teams' && isAdmin && <TeamsView />}
        {view === 'projects' && <ProjectsView currentUser={user} onProjectsChanged={loadProjects} />}
        {view === 'automation' && <AutomationView projects={projects} isAdmin={isAdmin} />}
        {view === 'approvals' && <ApprovalsView onChanged={loadApprovalCount} />}
        {view === 'usage' && <UsageView />}
        {view === 'security' && <SecurityView projects={projects} />}
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
      <CodeReviewModal
        open={reviewOpen}
        projects={projects}
        onClose={() => setReviewOpen(false)}
        onCreated={refresh}
      />
      <ChangePasswordModal open={pwOpen} onClose={() => setPwOpen(false)} />
      <TaskDetail
        taskId={selected}
        onClose={() => setSelected(null)}
        onChanged={refresh}
        onOpenTask={(id) => setSelected(id)}
      />
    </Layout>
  )
}
