import { useCallback, useEffect, useState } from 'react'
import {
  Card,
  Col,
  Empty,
  Row,
  Select,
  Space,
  Statistic,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import { api, FINDING_STATUS_COLORS, SEVERITY_COLORS } from '../api'

const STATUS_OPTS = [
  { value: 'open', label: 'Open' },
  { value: 'fixed', label: 'Fixed' },
  { value: 'accepted', label: 'Accepted (risk)' },
  { value: 'false_positive', label: 'False positive' },
]

export default function SecurityView({ projects = [] }) {
  const [findings, setFindings] = useState([])
  const [summary, setSummary] = useState(null)
  const [loading, setLoading] = useState(false)
  const [projectId, setProjectId] = useState()
  const [severity, setSeverity] = useState()
  const [status, setStatus] = useState('open')

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const [f, s] = await Promise.all([
        api.listFindings({ project_id: projectId, severity, status }),
        api.findingsSummary(projectId),
      ])
      setFindings(f)
      setSummary(s)
    } catch (e) {
      message.error(e.message)
    } finally {
      setLoading(false)
    }
  }, [projectId, severity, status])

  useEffect(() => {
    refresh()
  }, [refresh])

  const setFindingStatus = async (id, value) => {
    try {
      await api.updateFinding(id, { status: value })
      refresh()
    } catch (e) {
      message.error(e.message)
    }
  }

  const columns = [
    {
      title: 'Severity',
      dataIndex: 'severity',
      width: 100,
      render: (s) => <Tag color={SEVERITY_COLORS[s]}>{s}</Tag>,
    },
    {
      title: 'Finding',
      dataIndex: 'title',
      render: (t, row) => (
        <div>
          <b>{t}</b>
          {row.description && (
            <div style={{ fontSize: 12, color: '#8a7a6d' }}>{row.description}</div>
          )}
        </div>
      ),
    },
    {
      title: 'Location',
      dataIndex: 'file',
      width: 160,
      ellipsis: true,
      render: (f, row) =>
        f ? (
          <Typography.Text code style={{ fontSize: 12 }}>
            {f}
            {row.line ? `:${row.line}` : ''}
          </Typography.Text>
        ) : (
          '—'
        ),
    },
    {
      title: 'Category',
      dataIndex: 'category',
      width: 130,
      render: (c, row) => (
        <span>
          {row.cwe && <Tag>{row.cwe}</Tag>}
          {c && (
            <div style={{ fontSize: 11, color: '#8a7a6d' }}>{c}</div>
          )}
        </span>
      ),
    },
    {
      title: 'Seen',
      dataIndex: 'scans_count',
      width: 110,
      render: (n, row) => (
        <Tooltip
          title={`First seen ${dayjs(row.first_seen).format('MMM D')} · last ${dayjs(
            row.last_seen
          ).format('MMM D')}`}
        >
          {n <= 1 ? <Tag color="cyan">new</Tag> : <Tag>recurring ×{n}</Tag>}
        </Tooltip>
      ),
    },
    {
      title: 'Status',
      dataIndex: 'status',
      width: 170,
      render: (st, row) => (
        <Select
          size="small"
          value={st}
          style={{ width: 155 }}
          onChange={(v) => setFindingStatus(row.id, v)}
          options={STATUS_OPTS}
        />
      ),
    },
  ]

  return (
    <>
      {summary && (
        <Row gutter={12} style={{ marginBottom: 16 }}>
          <Col flex="1">
            <Card size="small">
              <Statistic
                title="Open critical"
                value={summary.open_critical}
                valueStyle={{ color: '#ff4d4f' }}
              />
            </Card>
          </Col>
          <Col flex="1">
            <Card size="small">
              <Statistic
                title="Open high"
                value={summary.open_high}
                valueStyle={{ color: '#fa541c' }}
              />
            </Card>
          </Col>
          <Col flex="1">
            <Card size="small">
              <Statistic title="Total open" value={summary.by_status?.open || 0} />
            </Card>
          </Col>
          <Col flex="1">
            <Card size="small">
              <Statistic title="Fixed" value={summary.by_status?.fixed || 0} />
            </Card>
          </Col>
          <Col flex="1">
            <Card size="small">
              <Statistic
                title="Accepted / FP"
                value={
                  (summary.by_status?.accepted || 0) +
                  (summary.by_status?.false_positive || 0)
                }
              />
            </Card>
          </Col>
        </Row>
      )}

      <Space style={{ marginBottom: 12 }} wrap>
        <Select
          allowClear
          placeholder="All projects"
          style={{ width: 180 }}
          value={projectId}
          onChange={setProjectId}
          options={projects.map((p) => ({ value: p.id, label: p.name }))}
        />
        <Select
          allowClear
          placeholder="Any severity"
          style={{ width: 150 }}
          value={severity}
          onChange={setSeverity}
          options={['critical', 'high', 'medium', 'low', 'info'].map((s) => ({
            value: s,
            label: s,
          }))}
        />
        <Select
          allowClear
          placeholder="Any status"
          style={{ width: 160 }}
          value={status}
          onChange={setStatus}
          options={STATUS_OPTS}
        />
        <ReloadOutlined onClick={refresh} style={{ cursor: 'pointer' }} />
      </Space>

      <Table
        rowKey="id"
        size="small"
        loading={loading}
        columns={columns}
        dataSource={findings}
        pagination={{ pageSize: 20, showSizeChanger: false }}
        locale={{
          emptyText: (
            <Empty
              description={
                status === 'open'
                  ? 'No open findings — run a Security Review to populate this.'
                  : 'No findings match.'
              }
            />
          ),
        }}
      />
    </>
  )
}
