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
  CopyOutlined,
  DeleteOutlined,
  DownloadOutlined,
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
      {thread.length > 1 && (
        <div style={{ marginBottom: 12 }}>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            <ApartmentOutlined /> Thread · {thread.length} steps
          </Typography.Text>
          <div style={{ marginTop: 6, display: 'flex', flexWrap: 'wrap', gap: 6 }}>
            {thread.map((step, i) => (
              <Button
                key={step.id}
                size="small"
                type={step.id === taskId ? 'primary' : 'default'}
                onClick={() => onOpenTask?.(step.id)}
                title={step.title}
              >
                {i + 1}. <Tag color={STATUS_COLORS[step.status]} style={{ marginInlineStart: 4 }}>
                  {step.status}
                </Tag>
              </Button>
            ))}
          </div>
        </div>
      )}

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
            {task.total_cost_usd != null ? `$${task.total_cost_usd}` : '—'}
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

      <Typography.Title level={5} style={{ marginTop: 16 }}>
        Result
      </Typography.Title>
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
