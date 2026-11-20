import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Alert,
  Button,
  Layout,
  Spin,
  Popconfirm,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import {
  DeleteOutlined,
  KeyOutlined,
  PlusOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
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
import HomeView from './home/HomeView'
import RunView from './runs/RunView'
import LoginPage from './auth/LoginPage'
import SetupPage from './auth/SetupPage'
import ChangePasswordModal from './auth/ChangePasswordModal'
import MyCredentials from './components/MyCredentials'
import { useThemeMode } from './theme/ThemeModeContext.jsx'
import { interceptHashLinks, redirectIfForbidden, returnTo, useRoute } from './shell/router'
import { buildCommands, isPaletteShortcut } from './shell/commands'
import Rail from './shell/Rail'
import TopBar from './shell/TopBar'
import CommandPalette from './shell/CommandPalette'

dayjs.extend(relativeTime)

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
  const [route, navigate] = useRoute()
  const page = route.page
  const [pwOpen, setPwOpen] = useState(false)
  const [credsOpen, setCredsOpen] = useState(false)
  const [tasks, setTasks] = useState([])
  const [stats, setStats] = useState(null)
  const [loading, setLoading] = useState(false)
  const [createOpen, setCreateOpen] = useState(false)
  const [reviewOpen, setReviewOpen] = useState(false)
  const [statusFilter, setStatusFilter] = useState()
  const { mode, setMode } = useThemeMode()
  const [projects, setProjects] = useState([])
  const [activeProjectId, setActiveProjectId] = useState(null)
  const [approvalCount, setApprovalCount] = useState(0)
  const [claudeInfo, setClaudeInfo] = useState(null)
  const [paletteOpen, setPaletteOpen] = useState(false)
  const [paletteData, setPaletteData] = useState({ workflows: [], recent: [], loading: false })
  const [runWorkflowId, setRunWorkflowId] = useState(null)

  // #/tasks/<id> opens that task's detail drawer.
  const selected = page === 'tasks' && route.id ? route.id : null
  // Opening a task pushes (Back returns to the list); switching steps inside
  // an already-open drawer replaces, so closing still steps back to the list.
  const openTask = useCallback(
    (id) => navigate({ page: 'tasks', id }, { replace: Boolean(selected) }),
    [navigate, selected],
  )
  useEffect(() => interceptHashLinks(navigate), [navigate])

  // Admin-only pages: a member following a stale link lands on Home.
  useEffect(() => {
    redirectIfForbidden({ page }, isAdmin, navigate)
  }, [isAdmin, page, navigate])

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
    if (page !== 'tasks') return
    refresh()
    const id = setInterval(refresh, 3000) // poll for live feed updates
    return () => clearInterval(id)
  }, [refresh, page])

  // ---- ⌘K ----
  const openPalette = useCallback(() => setPaletteOpen(true), [])
  const closePalette = useCallback(() => setPaletteOpen(false), [])

  useEffect(() => {
    const onKey = (e) => {
      if (!isPaletteShortcut(e)) return
      e.preventDefault()
      setPaletteOpen((o) => !o)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  // Workflows (and recent tasks, if the Tasks page hasn't loaded them) are
  // fetched lazily each time the palette opens.
  useEffect(() => {
    if (!paletteOpen) return
    let live = true
    setPaletteData((d) => ({ ...d, loading: true }))
    Promise.all([
      api.listWorkflows().catch(() => []),
      tasks.length ? Promise.resolve(null) : api.listTasks({ limit: 20 }).catch(() => []),
    ]).then(([workflows, recent]) => {
      if (live) setPaletteData((d) => ({ workflows, recent: recent ?? d.recent, loading: false }))
    })
    return () => {
      live = false
    }
  }, [paletteOpen]) // eslint-disable-line react-hooks/exhaustive-deps

  const commands = useMemo(
    () =>
      buildCommands({
        projects,
        tasks: (tasks.length ? tasks : paletteData.recent).slice(0, 20),
        workflows: paletteData.workflows,
        isAdmin,
        actions: {
          navigate,
          // No per-project page yet: show its tasks.
          openProject: (p) => {
            setActiveProjectId(p.id)
            navigate({ page: 'tasks' })
          },
          newTask: () => setCreateOpen(true),
          runWorkflow: (w) => {
            setRunWorkflowId(w.id)
            navigate({ page: 'automation' })
          },
          toggleNight: () => setMode(mode === 'night' ? 'light' : 'night'),
        },
      }),
    [projects, tasks, paletteData, isAdmin, navigate, mode, setMode],
  )
  const clearRunRequest = useCallback(() => setRunWorkflowId(null), [])

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
          <a href={`#/tasks/${encodeURIComponent(row.id)}`}>{t}</a>
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
    <div className="co-shell">
      <Rail page={page} isAdmin={isAdmin} approvalCount={approvalCount} />
      <div className="co-main">
        <TopBar
          user={user}
          claudeInfo={claudeInfo}
          isAdmin={isAdmin}
          onOpenPalette={openPalette}
          onOpenCredentials={() => setCredsOpen(true)}
          onChangePassword={() => setPwOpen(true)}
          onLogout={onLogout}
        />

        <main className="co-content" data-testid={`page-${page}`}>
          {noCredential && (
            <Alert
              data-testid="no-credential-banner"
              type="warning"
              showIcon
              style={{ marginBottom: 16 }}
              message="Claude isn't set up for you"
              description={
                isAdmin
                  ? 'Tasks you start will fail until you add your own Claude token or API key, or set an org fallback key for everyone on the Users page. Your schedules and PR auto-reviews are paused until then.'
                  : 'Tasks you start will fail until you add your own Claude token or API key (or an admin sets an org fallback key). Your schedules and PR auto-reviews are paused until then.'
              }
              action={
                <Space direction="vertical">
                  <Button size="small" type="primary" icon={<KeyOutlined />} onClick={() => setCredsOpen(true)}>
                    My credentials
                  </Button>
                  {isAdmin && (
                    <Button size="small" onClick={() => navigate({ page: 'users' })}>
                      Org fallback key
                    </Button>
                  )}
                </Space>
              }
            />
          )}
          {page === 'home' && (
            <HomeView
              user={user}
              navigate={navigate}
              onOpenCredentials={() => setCredsOpen(true)}
              onApprovalsChanged={loadApprovalCount}
              refreshSignal={claudeInfo?.you?.claude}
            />
          )}
          {page === 'runs' && (
            <RunView
              key={route.id}
              id={route.id}
              user={user}
              projects={projects}
              navigate={navigate}
              onOpenCredentials={() => setCredsOpen(true)}
            />
          )}
          {page === 'mcp' && <McpRegistry />}
          {page === 'users' && isAdmin && <UsersView currentUser={user} onFallbackChanged={loadClaudeInfo} />}
          {page === 'teams' && isAdmin && <TeamsView />}
          {page === 'projects' && <ProjectsView currentUser={user} onProjectsChanged={loadProjects} />}
          {page === 'automation' && (
            <AutomationView
              projects={projects}
              isAdmin={isAdmin}
              runWorkflowId={runWorkflowId}
              onRunHandled={clearRunRequest}
            />
          )}
          {page === 'approvals' && <ApprovalsView onChanged={loadApprovalCount} />}
          {page === 'usage' && <UsageView />}
          {page === 'security' && <SecurityView projects={projects} />}
          {page === 'tasks' && (
            <>
              <StatsBar stats={stats} />

              <Space style={{ marginBottom: 12, width: '100%', justifyContent: 'space-between' }} wrap>
                <Space wrap>
                  <Select
                    allowClear
                    placeholder="All projects"
                    style={{ width: 200 }}
                    value={activeProjectId}
                    onChange={(v) => setActiveProjectId(v || null)}
                    options={projects.map((p) => ({ value: p.id, label: p.name }))}
                    data-testid="task-project-filter"
                  />
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
                <Space wrap>
                  <Button icon={<SafetyCertificateOutlined />} onClick={() => setReviewOpen(true)}>
                    Security Review
                  </Button>
                  <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
                    New Task
                  </Button>
                </Space>
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
        </main>
      </div>

      <CommandPalette open={paletteOpen} commands={commands} loading={paletteData.loading} onClose={closePalette} />
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
        onClose={() => returnTo({ page: 'tasks' }, navigate)}
        onChanged={refresh}
        onOpenTask={openTask}
        onOpenCredentials={() => setCredsOpen(true)}
      />
    </div>
  )
}
