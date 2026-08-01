import { useCallback, useEffect, useState } from 'react'
import {
  Button,
  Card,
  Empty,
  Input,
  Modal,
  Space,
  Tag,
  Typography,
  message,
} from 'antd'
import {
  CheckOutlined,
  CloseOutlined,
  ReloadOutlined,
  SafetyOutlined,
} from '@ant-design/icons'
import dayjs from 'dayjs'
import { api, RISK_COLORS } from '../api'

export default function ApprovalsView({ onChanged }) {
  const [pending, setPending] = useState([])
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState(null)

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      setPending(await api.listApprovals())
      onChanged?.()
    } catch (e) {
      message.error(e.message)
    } finally {
      setLoading(false)
    }
  }, [onChanged])

  useEffect(() => {
    refresh()
    const id = setInterval(refresh, 5000)
    return () => clearInterval(id)
  }, [refresh])

  const approve = async (id) => {
    setBusy(id)
    try {
      await api.approveTask(id)
      message.success('Approved — task queued')
      refresh()
    } catch (e) {
      message.error(e.message)
    } finally {
      setBusy(null)
    }
  }

  const reject = (task) => {
    let reason = ''
    Modal.confirm({
      title: `Reject "${task.title}"?`,
      content: (
        <Input.TextArea
          rows={3}
          placeholder="Reason (optional, fed back to the audit trail)"
          onChange={(e) => (reason = e.target.value)}
        />
      ),
      okText: 'Reject',
      okButtonProps: { danger: true },
      onOk: async () => {
        await api.rejectTask(task.id, reason)
        message.success('Rejected')
        refresh()
      },
    })
  }

  const approveAll = async () => {
    setBusy('all')
    try {
      const done = await api.bulkApprove(pending.map((t) => t.id))
      message.success(`Approved ${done.length} task${done.length === 1 ? '' : 's'}`)
      refresh()
    } catch (e) {
      message.error(e.message)
    } finally {
      setBusy(null)
    }
  }

  return (
    <>
      <Space style={{ marginBottom: 16 }}>
        <Typography.Title level={5} style={{ margin: 0 }}>
          <SafetyOutlined /> Approval Inbox ({pending.length})
        </Typography.Title>
        <Button icon={<ReloadOutlined />} onClick={refresh} loading={loading}>
          Refresh
        </Button>
        {pending.length > 1 && (
          <Button loading={busy === 'all'} onClick={approveAll}>
            Approve all
          </Button>
        )}
      </Space>

      {pending.length === 0 ? (
        <Empty description="Nothing waiting for approval" />
      ) : (
        <Space direction="vertical" style={{ width: '100%' }} size={12}>
          {pending.map((t) => (
            <Card
              key={t.id}
              size="small"
              title={
                <Space>
                  <Tag color={RISK_COLORS[t.risk]}>{t.risk}</Tag>
                  <span>{t.title}</span>
                </Space>
              }
              extra={
                <Space>
                  <Button
                    type="primary"
                    icon={<CheckOutlined />}
                    loading={busy === t.id}
                    onClick={() => approve(t.id)}
                  >
                    Approve
                  </Button>
                  <Button danger icon={<CloseOutlined />} onClick={() => reject(t)}>
                    Reject
                  </Button>
                </Space>
              }
            >
              <Space direction="vertical" size={4} style={{ width: '100%' }}>
                <Typography.Text type="secondary">
                  {t.project ? `Project: ${t.project} · ` : ''}
                  {t.schedule_id ? 'From a schedule · ' : ''}
                  {dayjs(t.created_at).fromNow()}
                </Typography.Text>
                {t.context && (
                  <Space size={4} wrap>
                    {t.context.can_merge && <Tag color="red">⚠ can merge</Tag>}
                    {t.context.can_write && !t.context.can_merge && (
                      <Tag color="orange">can write</Tag>
                    )}
                    {(t.context.mcp_servers || []).map((srv) => (
                      <Tag key={srv} color="geekblue">
                        {srv}
                      </Tag>
                    ))}
                    {t.context.blocked_tools?.length > 0 && (
                      <Tag>{t.context.blocked_tools.length} tool(s) blocked</Tag>
                    )}
                    <Tag>mode: {t.context.permission_mode}</Tag>
                  </Space>
                )}
                <Typography.Paragraph
                  style={{ marginBottom: 0, whiteSpace: 'pre-wrap' }}
                  ellipsis={{ rows: 3, expandable: true, symbol: 'more' }}
                >
                  {t.prompt}
                </Typography.Paragraph>
              </Space>
            </Card>
          ))}
        </Space>
      )}
    </>
  )
}
