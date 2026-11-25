import { useCallback, useEffect, useRef, useState } from 'react'
import {
  Alert,
  Badge,
  Button,
  Drawer,
  Empty,
  Form,
  Input,
  InputNumber,
  List,
  Modal,
  Popconfirm,
  Segmented,
  Select,
  Skeleton,
  Space,
  Switch,
  Table,
  Tabs,
  Tag,
  Typography,
  message,
} from 'antd'
import {
  ApiOutlined,
  BulbOutlined,
  DatabaseOutlined,
  HistoryOutlined,
  DeleteOutlined,
  EditOutlined,
  PlusOutlined,
  ReloadOutlined,
  BookOutlined,
  RadarChartOutlined,
  SafetyOutlined,
  TeamOutlined,
  FileTextOutlined,
} from '@ant-design/icons'
import dayjs from 'dayjs'
import { when } from '../time'
import relativeTime from 'dayjs/plugin/relativeTime'
import { api } from '../api'
import ProjectMembers from './ProjectMembers'
import ProjectCards from '../projects/ProjectCards'
import LicenceBadge from '../projects/LicenceBadge'
import GuardSettings from '../guard/GuardSettings'
import { canEditGuard } from '../guard/guard'
import DecisionsPane from '../decisions/DecisionsPane'
import LaunchTab from '../launch/LaunchTab'
import CodeMapSection from '../codemap/CodeMapSection'
import ActivitySection from '../activity/ActivitySection'
import DatabaseSection from '../schema/DatabaseSection'
import RecommendedSection from '../recommend/RecommendedSection'
import { FRESH_SUGGEST, freshSessionError } from '../usage/usage'

dayjs.extend(relativeTime)

const MODEL_OPTS = [
  { value: 'sonnet', label: 'Sonnet' },
  { value: 'opus', label: 'Opus' },
  { value: 'haiku', label: 'Haiku' },
]

const CREATOR = '__creator__' // Select can't hold null: stands for "the project creator"

// Who may own a project's automation (webhook-triggered reviews): maintainers,
// directly or through a team, plus admins. We can only tell admins apart for
// ourselves, so the list is maintainers + you (if admin) + the current value.
async function loadOwnerOptions(project, currentUser) {
  const [members, users, teams] = await Promise.all([
    api.getProjectMembers(project.id),
    api.userDirectory(),
    api.listTeams().catch(() => []),
  ])
  const byId = new Map(users.map((u) => [u.id, u]))
  const label = (id, fallback) => {
    const u = byId.get(id)
    return u ? (u.name ? `${u.name} (${u.email})` : u.email) : fallback || id
  }
  const opts = new Map()
  for (const m of members) {
    if (m.role !== 'maintainer') continue
    if (m.subject_type === 'user') opts.set(m.subject_id, label(m.subject_id, m.label))
    else
      for (const u of teams.find((t) => t.id === m.subject_id)?.members || [])
        if (!opts.has(u.id)) opts.set(u.id, `${label(u.id, u.name || u.email)} — via ${m.label || 'team'}`)
  }
  if (currentUser?.role === 'admin' && !opts.has(currentUser.id))
    opts.set(currentUser.id, `${label(currentUser.id, currentUser.name)} — admin`)
  const cur = project.automation_owner_id
  if (cur && !opts.has(cur)) opts.set(cur, `${label(cur, 'Unknown user')} — no longer a maintainer`)
  return [
    { value: CREATOR, label: 'Project creator (default)' },
    ...[...opts].map(([value, l]) => ({ value, label: l })),
  ]
}

export function ProjectModal({ open, project, isAdmin, currentUser, onClose, onSaved }) {
  const [form] = Form.useForm()
  const [saving, setSaving] = useState(false)
  const [ownerOpts, setOwnerOpts] = useState([])
  const isEdit = !!project
  // token care (phase 3b): maintainers set it on an existing project; empty is off
  const editFresh = isEdit && (project.my_role === 'maintainer' || isAdmin)

  useEffect(() => {
    if (!open || !project) return
    setOwnerOpts([{ value: CREATOR, label: 'Project creator (default)' }])
    loadOwnerOptions(project, currentUser)
      .then(setOwnerOpts)
      .catch((e) => message.error(e.message))
  }, [open, project?.id])

  useEffect(() => {
    if (!open) return
    if (project) {
      form.setFieldsValue({
        name: project.name,
        ...(isAdmin ? { directory: project.directory, archived: project.archived } : {}),
        default_model: project.default_model,
        instructions: project.instructions,
        memory_enabled: project.memory_enabled,
        budget_usd: project.budget_usd,
        github_repo: project.github_repo,
        auto_review_prs: project.auto_review_prs,
        automation_owner_id: project.automation_owner_id || CREATOR,
        ...(editFresh ? { fresh_session_tokens: project.fresh_session_tokens ?? null } : {}),
      })
    } else {
      form.resetFields()
      form.setFieldsValue({ default_model: 'sonnet', memory_enabled: true })
    }
  }, [open, project])

  const submit = async () => {
    let v
    try {
      v = await form.validateFields()
    } catch {
      return
    }
    if (!isAdmin) {
      // Admin-only fields; the server would reject the whole PATCH with 403.
      delete v.directory
      delete v.archived
    }
    if (isEdit) {
      // Send the owner only when it changed, so an unrelated edit isn't refused
      // because the saved owner has since stopped being a maintainer.
      const owner = v.automation_owner_id === CREATOR ? null : v.automation_owner_id
      if (owner === (project.automation_owner_id || null)) delete v.automation_owner_id
      else v.automation_owner_id = owner
    } else delete v.automation_owner_id
    if (editFresh) v.fresh_session_tokens = v.fresh_session_tokens ?? null
    else delete v.fresh_session_tokens
    setSaving(true)
    try {
      if (isEdit) await api.updateProject(project.id, v)
      else {
        const made = await api.createProject(v)
        // co-guard's trust suggestion (change it in the project's Guard tab)
        if (made?.trust_reason)
          message.info(`Trust: ${made.trust === 'external' ? 'external' : 'own code'} — ${made.trust_reason}`, 8)
      }
      message.success(isEdit ? 'Project saved' : 'Project created')
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
      title={isEdit ? `Edit ${project.name}` : 'New Project'}
      open={open}
      onCancel={onClose}
      onOk={submit}
      confirmLoading={saving}
      width={560}
    >
      <Form form={form} layout="vertical">
        <Form.Item name="name" label="Name" rules={[{ required: true }]}>
          <Input placeholder="e.g. OpsMind" />
        </Form.Item>
        {isAdmin && (
          <Form.Item
            name="directory"
            label="Directory"
            extra="Absolute path to the repo/folder tasks run in. Defaults to ./projects/<slug>."
          >
            <Input placeholder="/Users/you/code/opsmind" disabled={isEdit} />
          </Form.Item>
        )}
        <div style={{ display: 'flex', gap: 12 }}>
          <Form.Item name="default_model" label="Default model" style={{ flex: 1 }}>
            <Select options={MODEL_OPTS} />
          </Form.Item>
          <Form.Item
            name="budget_usd"
            label="Budget (USD)"
            style={{ flex: 1 }}
            extra="Flags the project over budget in Usage."
          >
            <InputNumber min={0} step={1} prefix="$" style={{ width: '100%' }} placeholder="none" />
          </Form.Item>
        </div>
        <Form.Item
          name="instructions"
          label="Project instructions"
          extra="Injected into every task's context (does not overwrite the repo's CLAUDE.md)."
        >
          <Input.TextArea rows={3} placeholder="Conventions, constraints, context…" />
        </Form.Item>
        <Form.Item
          name="github_repo"
          label="GitHub repo"
          extra="owner/name — enables the webhook to auto-run reviews on this repo's PRs."
        >
          <Input placeholder="akuks/Opsunity-Hydra" />
        </Form.Item>
        {isEdit && (
          <Form.Item
            name="automation_owner_id"
            label="Automation owner"
            extra="Webhook-triggered PR reviews run as this person, on their Claude credential. Must be a maintainer."
          >
            <Select data-testid="automation-owner" showSearch optionFilterProp="label" options={ownerOpts} />
          </Form.Item>
        )}
        {editFresh && (
          <Form.Item
            name="fresh_session_tokens"
            label="Fresh session at (tokens)"
            data-testid="fresh-session"
            extra={`Empty is off. When a step's conversation reaches this size, the agent saves its notes and the thread continues in a fresh session (at most 3 times in a row). ${FRESH_SUGGEST.toLocaleString('en-US')} is a good start.`}
            rules={[
              {
                validator: (_, value) => {
                  const err = freshSessionError(value)
                  return err ? Promise.reject(new Error(err)) : Promise.resolve()
                },
              },
            ]}
          >
            <InputNumber
              data-testid="fresh-session-input"
              step={10000}
              precision={0}
              placeholder={String(FRESH_SUGGEST)}
              style={{ width: '100%' }}
            />
          </Form.Item>
        )}
        <Space size="large">
          <Form.Item name="memory_enabled" label="Living memory" valuePropName="checked">
            <Switch />
          </Form.Item>
          <Form.Item
            name="auto_review_prs"
            label="Auto-review PRs"
            valuePropName="checked"
            tooltip="Run a security review automatically when a PR is opened/updated (needs the GitHub webhook configured)."
          >
            <Switch />
          </Form.Item>
          {isEdit && isAdmin && (
            <Form.Item name="archived" label="Archived" valuePropName="checked">
              <Switch />
            </Form.Item>
          )}
        </Space>
      </Form>
    </Modal>
  )
}

function MemoryPane({ project, canEdit, onSaved }) {
  const [mem, setMem] = useState('')
  const [summaries, setSummaries] = useState([])
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!project) return
    api
      .getMemory(project.id)
      .then((m) => setMem(m.memory || ''))
      .catch((e) => message.error(e.message))
    api
      .listSummaries(project.id)
      .then(setSummaries)
      .catch((e) => message.error(e.message))
  }, [project?.id])

  const save = async () => {
    setBusy(true)
    try {
      await api.updateProject(project.id, { memory: mem })
      message.success('Memory saved')
      onSaved?.()
    } catch (e) {
      message.error(e.message)
    } finally {
      setBusy(false)
    }
  }

  const regenerate = async () => {
    setBusy(true)
    try {
      const m = await api.regenerateMemory(project.id)
      setMem(m.memory || '')
      message.success('Memory regenerated from task history')
      onSaved?.()
    } catch (e) {
      message.error(e.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <Typography.Paragraph type="secondary">
        Living memory is auto-updated after each task.
        {canEdit && " You can edit it directly, or regenerate it from the project's task history."}
      </Typography.Paragraph>
      {canEdit && (
        <Space style={{ marginBottom: 12 }}>
          <Button icon={<ReloadOutlined />} loading={busy} onClick={regenerate}>
            Regenerate
          </Button>
          <Button type="primary" loading={busy} onClick={save}>
            Save memory
          </Button>
        </Space>
      )}
      <Input.TextArea
        value={mem}
        readOnly={!canEdit}
        onChange={(e) => setMem(e.target.value)}
        autoSize={{ minRows: 8, maxRows: 20 }}
        style={{ fontFamily: 'ui-monospace, Menlo, monospace', fontSize: 13 }}
      />
      <Typography.Title level={5} style={{ marginTop: 20 }}>
        Task summaries ({summaries.length})
      </Typography.Title>
      {summaries.length ? (
        <List
          size="small"
          dataSource={summaries}
          renderItem={(sm) => (
            <List.Item>
              <List.Item.Meta
                title={sm.title}
                description={sm.summary}
              />
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                {when(sm.created_at).format('MMM D, HH:mm')}
              </Typography.Text>
            </List.Item>
          )}
        />
      ) : (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="No task history yet" />
      )}
    </>
  )
}

// Drawer line under the title: what the licence check found, and (maintainers) a way to run it again.
export function LicenceCheck({ project, canEdit, onSaved }) {
  const [busy, setBusy] = useState(false)
  if (!project) return null
  const again = async () => {
    setBusy(true)
    try {
      await api.refreshLicence(project.id)
      onSaved?.()
    } catch (e) {
      message.error(String(e?.message || 'Could not check the licence'))
    } finally {
      setBusy(false)
    }
  }
  const lic = project.licence
  const kind = lic?.kind
  const plain = !lic ? 'not checked yet' : kind === 'unknown' ? 'not recognised' : String(lic.id || lic.name || 'not recognised')
  return (
    <div style={{ marginBottom: 12 }} data-testid="licence-line">
      {kind === 'none' && !project.licence_badge ? (
        <Typography.Text type="secondary">No licence file found</Typography.Text>
      ) : (
        <>
          <Typography.Text type="secondary">Licence: </Typography.Text>
          <LicenceBadge project={project} />
          {!project.licence_badge && <Typography.Text type="secondary">{plain}</Typography.Text>}
        </>
      )}
      {canEdit && (
        <Typography.Link onClick={busy ? undefined : again} data-testid="licence-check-again" style={{ marginLeft: 8 }}>
          {busy ? 'Checking…' : 'Check again'}
        </Typography.Link>
      )}
    </div>
  )
}

// Per-project drawer: Members, Memory and Guard tabs.
// "No commits yet": a bad edit cannot be undone with git until the first commit.
export function GitStateWarning({ projectId }) {
  const [noCommits, setNoCommits] = useState(false)
  useEffect(() => {
    let live = true
    setNoCommits(false)
    if (projectId)
      Promise.resolve()
        .then(() => api.getGitState(projectId))
        .then((g) => live && setNoCommits(g?.no_commits === true))
        .catch(() => {})
    return () => {
      live = false
    }
  }, [projectId])
  if (!noCommits) return null
  return (
    <Alert
      type="warning"
      showIcon
      style={{ marginBottom: 12 }}
      data-testid="no-commits-warning"
      message="No commits yet"
      description="A bad edit cannot be undone with git until the first commit."
    />
  )
}

export function ProjectDrawer({ project, tab, onTab, currentUser, onClose, onSaved }) {
  const canEdit = project?.my_role === 'maintainer'
  const [pendingProposals, setPendingProposals] = useState(0)
  const projectId = project?.id
  // One cheap look per project at whether it has a user interface, to know whether to show "Recommended".
  // undefined = still loading (no tab yet), null = the check failed (tab shows a retry), else the answer.
  const [rec, setRec] = useState(undefined)
  const recFor = useRef(null) // only the latest project's answer may land
  const loadRec = useCallback(async () => {
    if (!projectId) return
    recFor.current = projectId
    try {
      const r = await api.getRecommended(projectId)
      if (recFor.current === projectId) setRec(r)
    } catch (_) {
      if (recFor.current === projectId) setRec(null)
    }
  }, [projectId])
  useEffect(() => {
    setRec(undefined)
    loadRec()
    return () => {
      recFor.current = null
    }
  }, [loadRec])
  // The open tab vanished (the project has no user interface): go back to the first tab.
  useEffect(() => {
    if (tab === 'recommended' && rec && !rec.has_ui) onTab('members')
  }, [tab, rec]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    let live = true
    setPendingProposals(0)
    if (projectId) {
      Promise.resolve(api.listProposals?.(projectId, 'pending'))
        .then((r) => live && setPendingProposals(Number(r?.pending) || 0))
        .catch(() => {})
    }
    return () => {
      live = false
    }
  }, [projectId])
  const [ruleDraft, setRuleDraft] = useState(null) // "Make this a rule": opens the project rule editor pre-filled

  return (
    <Drawer open={!!project} onClose={onClose} width={680} title={
        <Space>
          {project?.name || ''}
          <LicenceBadge project={project} />
        </Space>
      }
      destroyOnHidden
    >
      {project && <LicenceCheck project={project} canEdit={canEdit} onSaved={onSaved} />}
      {project && <GitStateWarning projectId={project.id} />}
      {project && (
        <Tabs
          activeKey={tab}
          onChange={onTab}
          items={[
            {
              key: 'members',
              label: (
                <span>
                  <TeamOutlined /> Members
                </span>
              ),
              children: <ProjectMembers project={project} currentUser={currentUser} onSaved={onSaved} />,
            },
            {
              key: 'memory',
              label: (
                <span>
                  <BookOutlined /> Memory
                </span>
              ),
              children: <MemoryPane project={project} canEdit={canEdit} onSaved={onSaved} />,
            },
            {
              key: 'decisions',
              label: (
                <span>
                  <FileTextOutlined /> Decisions
                  {pendingProposals > 0 && <Badge count={pendingProposals} size="small" offset={[6, -2]} data-testid="decisions-badge" />}
                </span>
              ),
              children: (
                <DecisionsPane
                  key={project.id}
                  project={project}
                  canEdit={canEditGuard(project, currentUser)}
                  isAdmin={currentUser?.role === 'admin'}
                  onCount={setPendingProposals}
                  onMakeRule={(d) => {
                    setRuleDraft(d)
                    onTab('guard')
                  }}
                />
              ),
            },
            {
              key: 'launch',
              label: (
                <span>
                  <FileTextOutlined /> Launch
                </span>
              ),
              children: <LaunchTab key={project.id} project={project} canEdit={canEditGuard(project, currentUser)} />,
            },
            {
              key: 'codemap',
              label: (
                <span>
                  <ApiOutlined /> Code map
                </span>
              ),
              children: <CodeMapSection key={project.id} active={tab === 'codemap'} project={project} canEdit={canEditGuard(project, currentUser)} />,
            },
            {
              key: 'database',
              label: (
                <span>
                  <DatabaseOutlined /> Database
                </span>
              ),
              children: <DatabaseSection key={project.id} active={tab === 'database'} project={project} />,
            },
            {
              key: 'activity',
              label: (
                <span>
                  <HistoryOutlined /> Activity
                </span>
              ),
              children: <ActivitySection key={project.id} active={tab === 'activity'} project={project} />,
            },
            ...(rec === null || rec?.has_ui
              ? [
                  {
                    key: 'recommended',
                    label: (
                      <span>
                        <BulbOutlined /> Recommended
                      </span>
                    ),
                    children: rec ? (
                      <RecommendedSection
                        key={project.id}
                        project={project}
                        data={rec}
                        reload={loadRec}
                        canEdit={canEditGuard(project, currentUser)}
                        isAdmin={currentUser?.role === 'admin'}
                      />
                    ) : (
                      <div data-testid="rec-failed">
                        Could not check this project.{' '}
                        <Button size="small" onClick={loadRec} data-testid="rec-retry">
                          Try again
                        </Button>
                      </div>
                    ),
                  },
                ]
              : []),
            {
              key: 'guard',
              label: (
                <span>
                  <SafetyOutlined /> Guard
                </span>
              ),
              children: <GuardSettings
                  project={project}
                  canEdit={canEditGuard(project, currentUser)}
                  onSaved={onSaved}
                  ruleDraft={ruleDraft}
                  onRuleDraftUsed={() => setRuleDraft(null)}
                />,
            },
          ]}
        />
      )}
    </Drawer>
  )
}

function DiscoverModal({ open, onClose, onImported }) {
  const [loading, setLoading] = useState(false)
  const [candidates, setCandidates] = useState([])
  const [selected, setSelected] = useState([])
  const [importing, setImporting] = useState(false)

  useEffect(() => {
    if (!open) return
    setSelected([])
    setLoading(true)
    api
      .discoverProjects()
      .then(setCandidates)
      .catch((e) => message.error(e.message))
      .finally(() => setLoading(false))
  }, [open])

  const doImport = async () => {
    setImporting(true)
    try {
      const created = await api.importProjects(selected)
      message.success(`Added ${created.length} project${created.length === 1 ? '' : 's'}`)
      const external = created.filter((p) => p.trust === 'external')
      if (external.length)
        message.info(
          `${external.length} came out external (someone else's code) and quarantined: ${external
            .map((p) => p.name)
            .join(', ')}. Change it in each project's Guard tab.`,
          8,
        )
      onImported?.()
      onClose()
    } catch (e) {
      message.error(e.message)
    } finally {
      setImporting(false)
    }
  }

  const addable = candidates.filter((c) => !c.already_added)

  return (
    <Modal
      title="Discover Claude projects"
      open={open}
      onCancel={onClose}
      width={760}
      footer={[
        <Button key="c" onClick={onClose}>
          Cancel
        </Button>,
        <Button
          key="a"
          type="primary"
          loading={importing}
          disabled={!selected.length}
          onClick={doImport}
        >
          Add {selected.length || ''} selected
        </Button>,
      ]}
    >
      <Typography.Paragraph type="secondary">
        Directories where you've run Claude Code (from <code>~/.claude/projects</code>).
        Pick the ones to manage as orchestrator projects.
      </Typography.Paragraph>
      <Table
        rowKey="directory"
        size="small"
        loading={loading}
        dataSource={candidates}
        pagination={false}
        scroll={{ y: 380 }}
        rowSelection={{
          selectedRowKeys: selected,
          onChange: setSelected,
          getCheckboxProps: (row) => ({ disabled: row.already_added }),
        }}
        columns={[
          {
            title: 'Project',
            dataIndex: 'name',
            render: (n, row) => (
              <Space>
                <b>{n}</b>
                {row.already_added && <Tag color="success">added</Tag>}
              </Space>
            ),
          },
          {
            title: 'Directory',
            dataIndex: 'directory',
            ellipsis: true,
            render: (d) => <Typography.Text type="secondary">{d}</Typography.Text>,
          },
          { title: 'Sessions', dataIndex: 'sessions', width: 80 },
          {
            title: 'Last used',
            dataIndex: 'last_active',
            width: 110,
            render: (d) => when(d).fromNow(),
          },
        ]}
      />
      {!loading && !addable.length && (
        <Typography.Text type="secondary">
          Nothing new to add — all discovered projects are already managed.
        </Typography.Text>
      )}
    </Modal>
  )
}

export default function ProjectsView({ currentUser, onProjectsChanged }) {
  const isAdmin = currentUser?.role === 'admin'
  const [projects, setProjects] = useState([])
  const [loading, setLoading] = useState(false)
  const [modalOpen, setModalOpen] = useState(false)
  const [discoverOpen, setDiscoverOpen] = useState(false)
  const [editing, setEditing] = useState(null)
  const [drawer, setDrawer] = useState(null) // {id, tab}
  const [view, setView] = useState('cards') // cards | list
  const [live, setLive] = useState([])
  const [schedules, setSchedules] = useState([])
  const [loaded, setLoaded] = useState(false)
  const drawerProject = drawer ? projects.find((p) => p.id === drawer.id) || null : null

  // Live work and paused schedules only decorate the cards: if either fails,
  // the cards still show (as idle / without the paused tag).
  const loadLive = useCallback(() => {
    api.liveRuns().then(setLive).catch(() => {})
  }, [])

  const refresh = useCallback(async () => {
    setLoading(true)
    loadLive()
    api.listSchedules().then(setSchedules).catch(() => {})
    try {
      setProjects(await api.listProjects(true))
      onProjectsChanged?.()
    } catch (e) {
      message.error(e.message)
    } finally {
      setLoading(false)
      setLoaded(true)
    }
  }, [onProjectsChanged, loadLive])

  useEffect(() => {
    refresh()
  }, [refresh])

  // Keep the mini comets current while the page is open.
  useEffect(() => {
    const t = setInterval(() => {
      if (document.visibilityState !== 'hidden') loadLive()
    }, 10000)
    return () => clearInterval(t)
  }, [loadLive])

  const editProject = (row) => {
    setEditing(row)
    setModalOpen(true)
  }
  const cardActions = (row) => (
    <>
      {row.my_role === 'maintainer' && (
        <Button size="small" type="text" icon={<EditOutlined />} title="Edit" aria-label={`Edit ${row.name}`} onClick={() => editProject(row)} />
      )}
      {isAdmin && (
        <Popconfirm
          title="Delete this project? Task history is removed; the directory is left untouched."
          onConfirm={() => remove(row.id)}
        >
          <Button size="small" type="text" danger icon={<DeleteOutlined />} title="Delete" aria-label={`Delete ${row.name}`} />
        </Popconfirm>
      )}
    </>
  )
  const maintained = projects.filter((p) => p.my_role === 'maintainer').length
  const emptyText = isAdmin ? 'No projects yet' : 'You are not a member of any project yet'

  const remove = async (id) => {
    try {
      await api.deleteProject(id)
      message.success('Project deleted')
      refresh()
    } catch (e) {
      message.error(e.message)
    }
  }

  const columns = [
    {
      title: 'Project',
      dataIndex: 'name',
      render: (n, row) => (
        <Space>
          <b>{n}</b>
          {row.archived && <Tag>archived</Tag>}
          {!isAdmin && (
            <Tag color={row.my_role === 'maintainer' ? 'gold' : 'default'}>{row.my_role}</Tag>
          )}
          {!row.memory_enabled && <Tag color="default">memory off</Tag>}
          {row.guard_enabled === false && <Tag color="warning">guard off</Tag>}
          {row.trust === 'external' && <Tag color="purple">external</Tag>}
          {row.quarantine && <Tag color="orange">quarantine</Tag>}
          <LicenceBadge project={row} />
        </Space>
      ),
    },
    {
      title: 'Directory',
      dataIndex: 'directory',
      ellipsis: true,
      render: (d) => <Typography.Text type="secondary">{d}</Typography.Text>,
    },
    { title: 'Model', dataIndex: 'default_model', width: 90 },
    { title: 'Tasks', dataIndex: 'task_count', width: 70 },
    {
      title: 'Cost',
      dataIndex: 'total_cost_usd',
      width: 90,
      render: (c) => (c ? `$${c.toFixed(4)}` : '—'),
    },
    {
      title: 'Actions',
      key: 'actions',
      width: 380,
      render: (_, row) => (
        <Space size="small">
          <Button size="small" icon={<TeamOutlined />} onClick={() => setDrawer({ id: row.id, tab: 'members' })}>
            Members
          </Button>
          <Button size="small" icon={<BookOutlined />} onClick={() => setDrawer({ id: row.id, tab: 'memory' })}>
            Memory
          </Button>
          <Button size="small" icon={<SafetyOutlined />} onClick={() => setDrawer({ id: row.id, tab: 'guard' })}>
            Guard
          </Button>
          {row.my_role === 'maintainer' && (
            <Button
              size="small"
              icon={<EditOutlined />}
              title="Edit"
              onClick={() => editProject(row)}
            />
          )}
          {isAdmin && (
            <Popconfirm
              title="Delete this project? Task history is removed; the directory is left untouched."
              onConfirm={() => remove(row.id)}
            >
              <Button size="small" danger icon={<DeleteOutlined />} title="Delete" />
            </Popconfirm>
          )}
        </Space>
      ),
    },
  ]

  return (
    <>
      <div className="co-projects-head">
        <div>
          <h1>Projects</h1>
          <div className="co-projects-sub co-num" data-testid="projects-summary">
            {loaded
              ? `${projects.length} project${projects.length === 1 ? '' : 's'}${
                  !isAdmin && maintained ? ` · you maintain ${maintained}` : ''
                }`
              : ' '}
          </div>
        </div>
        <Space wrap>
          <Segmented
            value={view}
            onChange={setView}
            options={[
              { value: 'cards', label: 'Cards' },
              { value: 'list', label: 'List' },
            ]}
            aria-label="Projects view"
            data-testid="projects-view-toggle"
          />
          {isAdmin && (
            <Button
              type="primary"
              icon={<PlusOutlined />}
              onClick={() => {
                setEditing(null)
                setModalOpen(true)
              }}
            >
              New Project
            </Button>
          )}
          {isAdmin && (
            <Button icon={<RadarChartOutlined />} onClick={() => setDiscoverOpen(true)}>
              Discover
            </Button>
          )}
          <Button icon={<ReloadOutlined />} onClick={refresh}>
            Refresh
          </Button>
        </Space>
      </div>

      {view === 'cards' ? (
        projects.length ? (
          <ProjectCards
            projects={projects}
            live={live}
            schedules={schedules}
            onOpen={(p) => setDrawer({ id: p.id, tab: 'members' })}
            renderActions={cardActions}
          />
        ) : loading && !loaded ? (
          <div className="co-surface co-projects-empty">
            <Skeleton active paragraph={{ rows: 3 }} />
          </div>
        ) : (
          <div className="co-surface co-projects-empty">
            <Empty description={emptyText} />
          </div>
        )
      ) : (
      <Table
        rowKey="id"
        loading={loading}
        columns={columns}
        dataSource={projects}
        pagination={false}
        locale={{ emptyText: <Empty description={emptyText} /> }}
      />
      )}

      <ProjectModal
        open={modalOpen}
        project={editing}
        isAdmin={isAdmin}
        currentUser={currentUser}
        onClose={() => setModalOpen(false)}
        onSaved={refresh}
      />
      <ProjectDrawer
        project={drawerProject}
        tab={drawer?.tab}
        onTab={(tab) => setDrawer((d) => ({ ...d, tab }))}
        currentUser={currentUser}
        onClose={() => setDrawer(null)}
        onSaved={refresh}
      />
      {isAdmin && (
        <DiscoverModal
          open={discoverOpen}
          onClose={() => setDiscoverOpen(false)}
          onImported={refresh}
        />
      )}
    </>
  )
}
