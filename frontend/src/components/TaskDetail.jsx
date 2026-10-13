import { useEffect, useRef, useState } from 'react'
import {
  Button,
  Descriptions,
  Drawer,
  Empty,
  Input,
  List,
  Popconfirm,
  Space,
  Tag,
  Typography,
  message,
} from 'antd'
import {
  ApartmentOutlined,
  CheckCircleFilled,
  ClockCircleFilled,
  CloseCircleFilled,
  CopyOutlined,
  DeleteOutlined,
  DownloadOutlined,
  ExclamationCircleFilled,
  LoadingOutlined,
  MinusCircleFilled,
  ReloadOutlined,
  SendOutlined,
  StopOutlined,
} from '@ant-design/icons'
import { api, PRIORITY_COLORS, STATUS_COLORS, shortModel } from '../api'
import { warm } from '../theme'

const EVENT_STYLE = {
  started: { color: '#8c8c8c', prefix: '▶' },
  system: { color: '#8c8c8c', prefix: '·' },
  text_output: { color: '#e6e6e6', prefix: '' },
  tool_use: { color: '#5ac8fa', prefix: '⚙' },
  tool_result: { color: '#7a7a7a', prefix: '↳' },
  log: { color: '#8c8c8c', prefix: '' },
  error: { color: '#ff4d4f', prefix: '✖' },
  completed: { color: '#52c41a', prefix: '✔' },
}

function renderLine(ev) {
  const s = EVENT_STYLE[ev.type] || { color: '#ccc', prefix: '' }
  const p = ev.payload || {}
  let text
  switch (ev.type) {
    case 'text_output':
      text = p.text
      break
    case 'tool_use':
      text = `${p.name}(${p.input ? JSON.stringify(p.input) : ''})`
      break
    case 'tool_result':
      text = p.content
      break
    case 'error':
      text = p.message
      break
    case 'completed':
      text = `completed — ${p.status}${p.num_turns ? `, ${p.num_turns} turns` : ''}${
        p.cost != null ? `, $${p.cost}` : ''
      }`
      break
    case 'started':
      text = `started (${p.model}, max ${p.max_turns} turns)`
      break
    case 'system':
      text = p.subtype || ''
      break
    default:
      text = JSON.stringify(p)
  }
  return { color: s.color, prefix: s.prefix, text: text || '' }
}

// Agent name is the part of the title before the first ": ".
function agentOf(t) {
  const i = (t.title || '').indexOf(': ')
  return i > 0 ? t.title.slice(0, i) : null
}
function titleBody(t) {
  const i = (t.title || '').indexOf(': ')
  return i > 0 ? t.title.slice(i + 2) : t.title
}

// Status → icon + colour for the workflow tree.
const STATUS_ICON = {
  completed: { icon: <CheckCircleFilled />, color: '#52c41a' },
  failed: { icon: <CloseCircleFilled />, color: '#ff4d4f' },
  running: { icon: <LoadingOutlined spin />, color: '#1677ff' },
  queued: { icon: <ClockCircleFilled />, color: '#8c8c8c' },
  awaiting_approval: { icon: <ExclamationCircleFilled />, color: '#faad14' },
  cancelled: { icon: <MinusCircleFilled />, color: '#8c8c8c' },
}
const fmtCost = (c) => (c == null ? null : `$${Number(c).toFixed(2)}`)

// A delegation/workflow tree built from parent_task_id links: coordinator at the
// top, parallel children + follow-ups nested under their parent with proper tree
// connector lines, a status icon, agent chip and clean cost — trackable at a glance.
function DelegationTree({ thread, currentId, onOpenTask }) {
  const GUIDE = 'rgba(150,130,110,0.35)'
  const byParent = {}
  const ids = new Set(thread.map((t) => t.id))
  for (const t of thread) {
    const pid = t.parent_task_id && ids.has(t.parent_task_id) ? t.parent_task_id : '__root__'
    ;(byParent[pid] = byParent[pid] || []).push(t)
  }
  const sortKey = (t) => t.created_at || ''

  const Node = ({ t, isChild }) => {
    const st = STATUS_ICON[t.status] || { icon: <ClockCircleFilled />, color: '#8c8c8c' }
    const agent = agentOf(t)
    const active = t.id === currentId
    const kids = (byParent[t.id] || []).sort((a, b) => sortKey(a).localeCompare(sortKey(b)))
    return (
      <div style={{ position: 'relative', paddingLeft: isChild ? 20 : 0 }}>
        {/* horizontal connector from the trunk to this node */}
        {isChild && (
          <span
            style={{
              position: 'absolute',
              left: 0,
              top: 15,
              width: 14,
              height: 1,
              background: GUIDE,
            }}
          />
        )}
        <div
          onClick={() => !active && onOpenTask?.(t.id)}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            padding: '5px 8px',
            borderRadius: 6,
            cursor: active ? 'default' : 'pointer',
            background: active ? 'rgba(255,170,100,0.14)' : 'transparent',
            border: active ? '1px solid rgba(255,170,100,0.4)' : '1px solid transparent',
            transition: 'background 0.12s',
          }}
          onMouseEnter={(e) => {
            if (!active) e.currentTarget.style.background = 'rgba(150,130,110,0.1)'
          }}
          onMouseLeave={(e) => {
            if (!active) e.currentTarget.style.background = 'transparent'
          }}
        >
          <span style={{ color: st.color, fontSize: 15, display: 'inline-flex' }}>{st.icon}</span>
          {agent ? (
            <Tag color="geekblue" style={{ margin: 0, fontWeight: 500 }}>
              {agent}
            </Tag>
          ) : (
            <Tag style={{ margin: 0, color: '#8a7a6d' }}>follow-up</Tag>
          )}
          <span
            style={{
              flex: 1,
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
              fontWeight: !isChild ? 600 : 400,
            }}
            title={titleBody(t)}
          >
            {titleBody(t)}
          </span>
          {fmtCost(t.total_cost_usd) && (
            <Typography.Text type="secondary" style={{ fontSize: 11, flexShrink: 0 }}>
              {fmtCost(t.total_cost_usd)}
            </Typography.Text>
          )}
        </div>
        {/* children: vertical trunk + recursive nodes */}
        {kids.length > 0 && (
          <div style={{ marginLeft: isChild ? 20 : 0, borderLeft: `1px solid ${GUIDE}`, paddingLeft: 0 }}>
            {kids.map((k) => (
              <Node key={k.id} t={k} isChild />
            ))}
          </div>
        )}
      </div>
    )
  }

  const roots = (byParent['__root__'] || []).sort((a, b) => sortKey(a).localeCompare(sortKey(b)))
  return (
    <div
      style={{
        border: '1px solid rgba(150,130,110,0.25)',
        borderRadius: 8,
        padding: 8,
        background: 'rgba(150,130,110,0.04)',
      }}
    >
      {roots.map((t) => (
        <Node key={t.id} t={t} isChild={false} />
      ))}
    </div>
  )
}

export default function TaskDetail({ taskId, onClose, onChanged, onOpenTask }) {
  const [task, setTask] = useState(null)
  const [events, setEvents] = useState([])
  const [artifacts, setArtifacts] = useState([])
  const [thread, setThread] = useState([])
  const [followup, setFollowup] = useState('')
  const [sending, setSending] = useState(false)
  const wsRef = useRef(null)
  const termRef = useRef(null)
  const lastSeqRef = useRef(0)

  // Load task + open the live stream whenever the selected task changes.
  useEffect(() => {
    if (!taskId) return
    let closed = false
    setEvents([])
    setArtifacts([])
    setFollowup('')
    lastSeqRef.current = 0

    api.getTask(taskId).then((t) => !closed && setTask(t))
    api.getThread(taskId).then((t) => !closed && setThread(t))

    const ws = new WebSocket(api.streamUrl(taskId, 0))
    wsRef.current = ws
    ws.onmessage = (e) => {
      const ev = JSON.parse(e.data)
      if (ev.seq) lastSeqRef.current = Math.max(lastSeqRef.current, ev.seq)
      setEvents((prev) => [...prev, ev])
      if (ev.type === 'completed' || ev.type === 'error') {
        api.getTask(taskId).then((t) => !closed && setTask(t))
        api.getArtifacts(taskId).then((a) => !closed && setArtifacts(a))
        onChanged?.()
      }
    }
    // Load any already-persisted artifacts (for finished tasks reopened later).
    api.getArtifacts(taskId).then((a) => !closed && setArtifacts(a))

    return () => {
      closed = true
      ws.close()
    }
  }, [taskId])

  // Auto-scroll the terminal as events arrive.
  useEffect(() => {
    if (termRef.current) termRef.current.scrollTop = termRef.current.scrollHeight
  }, [events])

  const act = async (fn, label) => {
    try {
      await fn(taskId)
      message.success(label)
      const t = await api.getTask(taskId)
      setTask(t)
      onChanged?.()
    } catch (e) {
      message.error(e.message)
    }
  }

  const sendFollowup = async () => {
    if (!followup.trim()) return
    setSending(true)
    try {
      const child = await api.followupTask(taskId, followup.trim())
      message.success('Follow-up started')
      setFollowup('')
      onChanged?.()
      onOpenTask?.(child.id) // switch the drawer to the new run in the thread
    } catch (e) {
      message.error(e.message)
    } finally {
      setSending(false)
    }
  }

  const isActive = task && ['queued', 'running', 'awaiting_approval'].includes(task.status)
  const canFollowUp = task && !isActive && task.session_id

  return (
    <Drawer
      open={!!taskId}
      onClose={onClose}
      width={720}
      title={task ? task.title : 'Task'}
      extra={
        <Space>
          {isActive && (
            <Button
              danger
              icon={<StopOutlined />}
              onClick={() => act(api.cancelTask, 'Cancelled')}
            >
              Cancel
            </Button>
          )}
          {task && !isActive && (
            <Button icon={<ReloadOutlined />} onClick={() => act(api.retryTask, 'Retrying')}>
              Retry
            </Button>
          )}
          <Button icon={<CopyOutlined />} onClick={() => act(api.duplicateTask, 'Duplicated')}>
            Duplicate
          </Button>
          {task && !isActive && (
            <Popconfirm
              title="Delete this task?"
              onConfirm={async () => {
                try {
                  await api.deleteTask(taskId)
                  message.success('Task deleted')
                  onChanged?.()
                  onClose()
                } catch (e) {
                  message.error(e.message)
                }
              }}
            >
              <Button danger icon={<DeleteOutlined />} />
            </Popconfirm>
          )}
        </Space>
      }
    >
      {thread.length > 1 &&
        (() => {
          const ids = new Set(thread.map((t) => t.id))
          const delegated = thread.some(
            (t) => t.parent_task_id && ids.has(t.parent_task_id) && t.id !== taskId
          )
          const done = thread.filter((t) => t.status === 'completed').length
          const failed = thread.filter((t) => t.status === 'failed').length
          const active = thread.filter((t) =>
            ['queued', 'running', 'awaiting_approval'].includes(t.status)
          ).length
          return (
            <div style={{ marginBottom: 12 }}>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                <ApartmentOutlined /> {delegated ? 'Delegated work' : 'Thread'} · {thread.length} tasks
                {' · '}
                {done} done{failed ? `, ${failed} failed` : ''}
                {active ? `, ${active} active` : ''}
              </Typography.Text>
              <div style={{ marginTop: 6 }}>
                {delegated ? (
                  <DelegationTree thread={thread} currentId={taskId} onOpenTask={onOpenTask} />
                ) : (
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                    {thread.map((step, i) => (
                      <Button
                        key={step.id}
                        size="small"
                        type={step.id === taskId ? 'primary' : 'default'}
                        onClick={() => onOpenTask?.(step.id)}
                        title={step.title}
                      >
                        {i + 1}.{' '}
                        <Tag color={STATUS_COLORS[step.status]} style={{ marginInlineStart: 4 }}>
                          {step.status}
                        </Tag>
                      </Button>
                    ))}
                  </div>
                )}
              </div>
            </div>
          )
        })()}

      {task && (
        <Descriptions size="small" column={2} bordered style={{ marginBottom: 16 }}>
          <Descriptions.Item label="Status">
            <Tag color={STATUS_COLORS[task.status]}>{task.status}</Tag>
          </Descriptions.Item>
          <Descriptions.Item label="Priority">
            <Tag color={PRIORITY_COLORS[task.priority]}>{task.priority}</Tag>
          </Descriptions.Item>
          <Descriptions.Item label="Model">
            {shortModel(task.model_used) || task.model || 'default'}
            {task.model_used && task.model && shortModel(task.model_used) !== task.model && (
              <Typography.Text type="secondary"> (requested {task.model})</Typography.Text>
            )}
          </Descriptions.Item>
          <Descriptions.Item label="Project">{task.project || '—'}</Descriptions.Item>
          <Descriptions.Item label="Turns">{task.num_turns ?? '—'}</Descriptions.Item>
          <Descriptions.Item label="Cost">
            {task.total_cost_usd != null ? fmtCost(task.total_cost_usd) : '—'}
          </Descriptions.Item>
          <Descriptions.Item label="Prompt" span={2}>
            <Typography.Text style={{ whiteSpace: 'pre-wrap' }}>{task.prompt}</Typography.Text>
          </Descriptions.Item>
          {task.error && (
            <Descriptions.Item label="Error" span={2}>
              <Typography.Text type="danger" style={{ whiteSpace: 'pre-wrap' }}>
                {task.error}
              </Typography.Text>
            </Descriptions.Item>
          )}
        </Descriptions>
      )}

      <Typography.Title level={5}>Live Output</Typography.Title>
      <div
        ref={termRef}
        style={{
          background: warm.bgTerminal,
          border: `1px solid ${warm.border}`,
          borderRadius: 6,
          padding: 12,
          height: 320,
          overflowY: 'auto',
          fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
          fontSize: 12,
          lineHeight: 1.5,
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-word',
        }}
      >
        {events.length === 0 && (
          <span style={{ color: warm.textMuted }}>Waiting for output…</span>
        )}
        {events.map((ev, i) => {
          const line = renderLine(ev)
          return (
            <div key={i} style={{ color: line.color }}>
              {line.prefix ? `${line.prefix} ` : ''}
              {line.text}
            </div>
          )
        })}
      </div>

      <Space
        style={{ marginTop: 16, width: '100%', justifyContent: 'space-between' }}
        align="center"
      >
        <Typography.Title level={5} style={{ margin: 0 }}>
          Result
        </Typography.Title>
        {task?.result_text && (
          <Space size="small">
            <a href={api.reportUrl(taskId, 'pdf')} target="_blank" rel="noreferrer">
              <Button size="small" icon={<DownloadOutlined />}>
                PDF
              </Button>
            </a>
            <a href={api.reportUrl(taskId, 'docx')} target="_blank" rel="noreferrer">
              <Button size="small" icon={<DownloadOutlined />}>
                DOCX
              </Button>
            </a>
          </Space>
        )}
      </Space>
      {task?.result_text ? (
        <div
          style={{
            background: warm.bgElevated,
            border: `1px solid ${warm.border}`,
            borderRadius: 6,
            padding: 12,
            whiteSpace: 'pre-wrap',
            fontSize: 13,
          }}
        >
          {task.result_text}
        </div>
      ) : (
        <Typography.Text type="secondary">No result yet.</Typography.Text>
      )}

      <Typography.Title level={5} style={{ marginTop: 16 }}>
        Artifacts ({artifacts.length})
      </Typography.Title>
      {artifacts.length ? (
        <List
          size="small"
          dataSource={artifacts}
          renderItem={(a) => (
            <List.Item
              actions={[
                <a
                  key="dl"
                  href={api.artifactUrl(taskId, a.rel_path)}
                  target="_blank"
                  rel="noreferrer"
                >
                  <DownloadOutlined /> download
                </a>,
              ]}
            >
              <List.Item.Meta
                title={a.rel_path}
                description={`${a.size} bytes${a.mime ? ` · ${a.mime}` : ''}`}
              />
            </List.Item>
          )}
        />
      ) : (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="No artifacts" />
      )}

      <Typography.Title level={5} style={{ marginTop: 16 }}>
        Follow up
      </Typography.Title>
      {canFollowUp ? (
        <Space.Compact style={{ width: '100%' }}>
          <Input.TextArea
            autoSize={{ minRows: 1, maxRows: 4 }}
            placeholder="Continue this thread — Claude resumes with full context…"
            value={followup}
            onChange={(e) => setFollowup(e.target.value)}
            onPressEnter={(e) => {
              if (!e.shiftKey) {
                e.preventDefault()
                sendFollowup()
              }
            }}
          />
          <Button
            type="primary"
            icon={<SendOutlined />}
            loading={sending}
            onClick={sendFollowup}
          >
            Send
          </Button>
        </Space.Compact>
      ) : (
        <Typography.Text type="secondary">
          {isActive
            ? 'Available once this run finishes.'
            : 'This task has no resumable session to continue.'}
        </Typography.Text>
      )}
    </Drawer>
  )
}
