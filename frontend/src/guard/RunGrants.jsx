import { useCallback, useEffect, useState } from 'react'
import { Alert, Button, Empty, Popconfirm, Space, Table, Tag, Tooltip, Typography, message } from 'antd'
import { KeyOutlined, ReloadOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import relativeTime from 'dayjs/plugin/relativeTime'
import { api } from '../api'
import { when } from '../time'
import { REVOKE_NOTE, grantCovers, ruleName } from './guard'
import './guard.css'

dayjs.extend(relativeTime)

// "Active run grants": approvals made "for this run" (and follow-up once
// grants) that are still live. rootId: one thread only. Revoke stops new
// requests and later steps at once; a running step keeps its copy.
export default function RunGrants({ rootId, refreshSignal }) {
  const [grants, setGrants] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const list = await api.listGrants(rootId)
      setGrants(Array.isArray(list) ? list : [])
      setError(null)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [rootId])
  useEffect(() => {
    load()
  }, [load, refreshSignal])

  const revoke = async (g) => {
    setBusy(g.id)
    try {
      await api.revokeGrant(g.id)
      message.success('Grant revoked')
    } catch (e) {
      message.error(e.message)
    } finally {
      setBusy(null)
    }
    load()
  }

  return (
    <section className="co-guard-section" data-testid="run-grants" aria-label="Active run grants">
      <Space style={{ marginBottom: 4 }}>
        <Typography.Title level={5} style={{ margin: 0 }}>
          <KeyOutlined /> Active run grants ({grants.length})
        </Typography.Title>
        <Button size="small" icon={<ReloadOutlined />} onClick={load} loading={loading}>
          Refresh
        </Button>
      </Space>
      <Typography.Paragraph type="secondary" className="co-guard-sub" data-testid="run-grants-note">
        Approvals made for a whole run: they let the same rules through in that thread until they expire. {REVOKE_NOTE}
      </Typography.Paragraph>
      {error && <Alert type="error" showIcon message={error} style={{ marginBottom: 8 }} />}
      <Table
        rowKey="id"
        size="small"
        pagination={false}
        loading={loading && !grants.length}
        dataSource={grants}
        scroll={{ x: true }}
        locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="No active grants" /> }}
        columns={[
          {
            title: 'Rule',
            dataIndex: 'rule_id',
            render: (r) => (
              <Tooltip title={r}>
                <Tag color="orange" bordered={false}>
                  {ruleName(r)}
                </Tag>
              </Tooltip>
            ),
          },
          {
            title: 'Covers',
            key: 'covers',
            render: (_, g) => (
              <span>
                {grantCovers(g)}
                {g.scope === 'once' ? (
                  <Tag bordered={false} style={{ marginLeft: 6 }}>
                    once, follow-up
                  </Tag>
                ) : null}
              </span>
            ),
          },
          {
            title: 'Thread',
            dataIndex: 'root_id',
            render: (id) => (
              <a href={`#/tasks/${encodeURIComponent(id)}`} className="co-guard-mono">
                {String(id).slice(0, 8)}
              </a>
            ),
          },
          {
            title: 'Used',
            dataIndex: 'use_count',
            width: 64,
            render: (n) => <span className="co-num">{n || 0}</span>,
          },
          {
            title: 'Expires',
            dataIndex: 'expires_at',
            render: (t) => <Tooltip title={when(t).format('YYYY-MM-DD HH:mm')}>{when(t).fromNow()}</Tooltip>,
          },
          {
            title: '',
            key: 'revoke',
            width: 96,
            render: (_, g) =>
              g.can_revoke ? (
                <Popconfirm
                  title="Revoke this grant?"
                  description="A running step keeps it until it ends."
                  okText="Revoke"
                  okButtonProps={{ danger: true }}
                  onConfirm={() => revoke(g)}
                >
                  <Button size="small" danger loading={busy === g.id} data-testid={`grant-revoke-${g.id}`}>
                    Revoke
                  </Button>
                </Popconfirm>
              ) : null,
          },
        ]}
      />
    </section>
  )
}
