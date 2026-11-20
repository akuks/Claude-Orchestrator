import { useCallback, useEffect, useRef, useState } from 'react'
import {
  Alert,
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
  KeyOutlined,
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
import MyCredentials from './components/MyCredentials'

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
  const [credsOpen, setCredsOpen] = useState(false)
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

  const loadClaudeInfo = useCallback(() => {
    api.claudeStatus().then(setClaudeInfo).catch(() => setClaudeInfo(null))
  }, [])
  useEffect(() => {
    loadClaudeInfo()
  }, [loadClaudeInfo])
  // Credential changes made in "My credentials" update the header tag/banner
  // without re-running the server's CLI check.
  const onCredsChanged = useCallback((you) => {
    setClaudeInfo((ci) => (ci ? { ...ci, you, authenticated: you.claude !== 'none' } : ci))
  }, [])
  const noCredential = claudeInfo?.you?.claude === 'none'

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
          {claudeInfo && <ClaudeTag info={claudeInfo} isAdmin={isAdmin} onClick={() => setCredsOpen(true)} />}
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
                { key: 'creds', icon: <KeyOutlined />, label: 'My credentials', onClick: () => setCredsOpen(true) },
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
        {noCredential && (
          <Alert
            data-testid="no-credential-banner"
            type="warning"
            showIcon
            style={{ marginBottom: 16 }}
            message="Claude isn't set up for you"
            description={
              isAdmin
                ? 'Tasks you start will fail until you add your own Claude token or API key, or set an org fallback key for everyone on the Users page.'
                : 'Tasks you start will fail until you add your own Claude token or API key (or an admin sets an org fallback key).'
            }
            action={
              <Space direction="vertical">
                <Button size="small" type="primary" icon={<KeyOutlined />} onClick={() => setCredsOpen(true)}>
                  My credentials
                </Button>
                {isAdmin && (
                  <Button size="small" onClick={() => setView('users')}>
                    Org fallback key
                  </Button>
                )}
              </Space>
            }
          />
        )}
        {view === 'mcp' && <McpRegistry />}
        {view === 'users' && isAdmin && <UsersView currentUser={user} onFallbackChanged={loadClaudeInfo} />}
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
      <MyCredentials open={credsOpen} onClose={() => setCredsOpen(false)} onChanged={onCredsChanged} />
      <TaskDetail
        taskId={selected}
        onClose={() => setSelected(null)}
        onChanged={refresh}
        onOpenTask={(id) => setSelected(id)}
        onOpenCredentials={() => setCredsOpen(true)}
      />
    </Layout>
  )
}

// Header tag: whether *you* can run tasks (your own Claude credential, the org
// fallback, or none), plus a server-side note if the CLI binary is missing.
function ClaudeTag({ info, isAdmin, onClick }) {
  if (!info.found)
    return (
      <Tag color="red" title="The claude CLI isn't installed on the server (check CO_CLAUDE_BIN).">
        claude CLI not found
      </Tag>
    )
  const you = info.you?.claude
  const version = info.version?.split(' ')[0]
  const title = version ? `claude ${version} on the server` : undefined
  if (you === 'own')
    return (
      <Tag color="green" title={title} style={{ cursor: 'pointer' }} onClick={onClick}>
        Claude: {info.you.claude_kind === 'api_key' ? 'your API key' : 'your token'}
      </Tag>
    )
  if (you === 'fallback')
    return (
      <Tag color="blue" title={title} style={{ cursor: 'pointer' }} onClick={onClick}>
        Claude: org fallback
      </Tag>
    )
  return (
    <Tag
      color="red"
      data-testid="claude-not-set-up"
      style={{ cursor: 'pointer' }}
      onClick={onClick}
      title={
        isAdmin
          ? 'Add your own credential under My credentials, or set an org fallback key on the Users page'
          : 'Add your own credential under My credentials (or ask an admin to set an org fallback key)'
      }
    >
      Claude: not set up
    </Tag>
  )
}
