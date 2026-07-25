import { useCallback, useEffect, useState } from 'react'
import {
  Button,
  Card,
  Col,
  Drawer,
  Empty,
  Popconfirm,
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
import {
  ApiOutlined,
  DeleteOutlined,
  EditOutlined,
  PlusOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons'
import { api, MCP_STATUS_COLORS } from '../api'
import McpServerModal from './McpServerModal'

function ObservabilityPanel({ obs }) {
  if (!obs) return null
  return (
    <Card size="small" style={{ marginBottom: 16 }} title={`MCP Activity (last ${obs.window_days}d)`}>
      <Row gutter={16}>
        <Col flex="0 0 140px">
          <Statistic title="Total Calls" value={obs.total_calls} />
        </Col>
        <Col flex="0 0 140px">
          <Statistic
            title="Failure Rate"
            value={Math.round((obs.failure_rate || 0) * 100)}
            suffix="%"
            valueStyle={{ color: obs.failure_rate > 0.1 ? '#ff4d4f' : '#52c41a' }}
          />
        </Col>
        <Col flex="auto">
          <Typography.Text type="secondary">Top tools</Typography.Text>
          <div style={{ marginTop: 4 }}>
            {obs.top_tools.length ? (
              obs.top_tools.slice(0, 6).map((t) => (
                <Tag key={`${t.server}.${t.tool}`}>
                  {t.server}.{t.tool} · {t.calls}
                </Tag>
              ))
            ) : (
              <Typography.Text type="secondary">no calls yet</Typography.Text>
            )}
          </div>
        </Col>
      </Row>
    </Card>
  )
}

function PolicyDrawer({ server, onClose }) {
  const [policies, setPolicies] = useState([])
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (server) api.getPolicies(server.id).then(setPolicies)
  }, [server])

  const setAction = (tool, action) =>
    setPolicies((prev) =>
      prev.map((p) => (p.tool_name === tool ? { ...p, action } : p))
    )

  const save = async () => {
    setSaving(true)
    try {
      await api.setPolicies(
        server.id,
        policies.map((p) => ({
          tool_name: p.tool_name,
          classification: p.classification,
          action: p.action,
        }))
      )
      message.success('Policies saved')
      onClose()
    } catch (e) {
      message.error(e.message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <Drawer
      open={!!server}
      onClose={onClose}
      width={560}
      title={server ? `Tool policies — ${server.name}` : ''}
      extra={
        <Button type="primary" loading={saving} onClick={save}>
          Save
        </Button>
      }
    >
      <Typography.Paragraph type="secondary">
        Auto-approve read-only tools, route writes through approval, or block a tool
        entirely. Policies are enforced when a task uses this server.
      </Typography.Paragraph>
      {policies.length ? (
        <Table
          rowKey="tool_name"
          size="small"
          pagination={false}
          dataSource={policies}
          columns={[
            { title: 'Tool', dataIndex: 'tool_name' },
            {
              title: 'Class',
              dataIndex: 'classification',
              width: 90,
              render: (c) => <Tag>{c}</Tag>,
            },
            {
              title: 'Action',
              dataIndex: 'action',
              width: 190,
              render: (action, row) => (
                <Select
                  size="small"
                  style={{ width: 180 }}
                  value={action}
                  onChange={(v) => setAction(row.tool_name, v)}
                  options={[
                    { value: 'auto_approve', label: 'Auto-approve' },
                    { value: 'require_approval', label: 'Require approval' },
                    { value: 'block', label: 'Block' },
                  ]}
                />
              ),
            },
          ]}
        />
      ) : (
        <Empty description="No tools discovered yet — run Test first." />
      )}
    </Drawer>
  )
}

export default function McpRegistry() {
  const [servers, setServers] = useState([])
  const [obs, setObs] = useState(null)
  const [loading, setLoading] = useState(false)
  const [testingId, setTestingId] = useState(null)
  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState(null)
  const [policyServer, setPolicyServer] = useState(null)

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const [s, o] = await Promise.all([api.listServers(), api.mcpObservability(7)])
      setServers(s)
      setObs(o)
    } catch (e) {
      message.error(e.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  const test = async (id) => {
    setTestingId(id)
    try {
      const r = await api.testServer(id)
      if (r.ok) message.success(`Connected — ${r.tools.length} tools discovered`)
      else message.warning(r.error || 'Could not connect')
      refresh()
    } catch (e) {
      message.error(e.message)
    } finally {
      setTestingId(null)
    }
  }

  const remove = async (id) => {
    try {
      await api.deleteServer(id)
      message.success('Server removed')
      refresh()
    } catch (e) {
      message.error(e.message)
    }
  }

  const columns = [
    {
      title: 'Name',
      dataIndex: 'name',
      render: (n, row) => (
        <Space>
          <ApiOutlined />
          <b>{n}</b>
          {!row.enabled && <Tag>disabled</Tag>}
        </Space>
      ),
    },
    { title: 'Transport', dataIndex: 'transport', width: 90 },
    {
      title: 'Scope',
      dataIndex: 'scope',
      width: 110,
      render: (s, row) => (
        <Tag>{s === 'project' ? `project:${row.project}` : s}</Tag>
      ),
    },
    {
      title: 'Status',
      dataIndex: 'status',
      width: 130,
      render: (s, row) => (
        <Tooltip title={row.status_detail}>
          <Tag color={MCP_STATUS_COLORS[s]}>{s}</Tag>
        </Tooltip>
      ),
    },
    {
      title: 'Tools',
      dataIndex: 'tools',
      width: 70,
      render: (t) => (t?.length ? t.length : '—'),
    },
    {
      title: 'Secrets',
      key: 'secrets',
      width: 90,
      render: (_, row) =>
        row.has_env || row.has_headers ? (
          <Tag color="gold" icon={<SafetyCertificateOutlined />}>
            vault
          </Tag>
        ) : (
          '—'
        ),
    },
    {
      title: 'Actions',
      key: 'actions',
      width: 230,
      render: (_, row) => (
        <Space size="small">
          <Button
            size="small"
            icon={<ThunderboltOutlined />}
            loading={testingId === row.id}
            onClick={() => test(row.id)}
          >
            Test
          </Button>
          <Button size="small" onClick={() => setPolicyServer(row)}>
            Policies
          </Button>
          <Button
            size="small"
            icon={<EditOutlined />}
            onClick={() => {
              setEditing(row)
              setModalOpen(true)
            }}
          />
          <Popconfirm title="Remove this server?" onConfirm={() => remove(row.id)}>
            <Button size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <>
      <ObservabilityPanel obs={obs} />

      <Space style={{ marginBottom: 12 }}>
        <Button
          type="primary"
          icon={<PlusOutlined />}
          onClick={() => {
            setEditing(null)
            setModalOpen(true)
          }}
        >
          Add MCP Server
        </Button>
        <Button icon={<ReloadOutlined />} onClick={refresh}>
          Refresh
        </Button>
      </Space>

      <Table
        rowKey="id"
        loading={loading}
        columns={columns}
        dataSource={servers}
        pagination={false}
        locale={{ emptyText: <Empty description="No MCP servers yet" /> }}
      />

      <McpServerModal
        open={modalOpen}
        server={editing}
        onClose={() => setModalOpen(false)}
        onSaved={refresh}
      />
      <PolicyDrawer server={policyServer} onClose={() => setPolicyServer(null)} />
    </>
  )
}
