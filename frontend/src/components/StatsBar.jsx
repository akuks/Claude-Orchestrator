import { Card, Col, Row, Statistic } from 'antd'
import ReactECharts from 'echarts-for-react'

const STATUS_COLOR = {
  completed: '#52c41a',
  failed: '#ff4d4f',
  running: '#1668dc',
  queued: '#8c8c8c',
  cancelled: '#595959',
  awaiting_approval: '#faad14',
}

export default function StatsBar({ stats }) {
  if (!stats) return null
  const entries = Object.entries(stats.by_status || {})
  const donut = {
    tooltip: { trigger: 'item' },
    series: [
      {
        type: 'pie',
        radius: ['55%', '80%'],
        avoidLabelOverlap: false,
        label: { show: false },
        data: entries.map(([k, v]) => ({
          name: k,
          value: v,
          itemStyle: { color: STATUS_COLOR[k] || '#888' },
        })),
      },
    ],
  }

  return (
    <Row gutter={12} style={{ marginBottom: 16 }}>
      <Col flex="1">
        <Card size="small">
          <Statistic title="Tasks Today" value={stats.tasks_today} />
        </Card>
      </Col>
      <Col flex="1">
        <Card size="small">
          <Statistic title="Running" value={stats.running} valueStyle={{ color: '#1668dc' }} />
        </Card>
      </Col>
      <Col flex="1">
        <Card size="small">
          <Statistic title="Queued" value={stats.queued} />
        </Card>
      </Col>
      <Col flex="1">
        <Card size="small">
          <Statistic
            title="Success Rate"
            value={Math.round((stats.success_rate || 0) * 100)}
            suffix="%"
            valueStyle={{ color: '#52c41a' }}
          />
        </Card>
      </Col>
      <Col flex="1">
        <Card size="small">
          <Statistic
            title="Avg Duration"
            value={stats.avg_duration_ms ? (stats.avg_duration_ms / 1000).toFixed(1) : '—'}
            suffix={stats.avg_duration_ms ? 's' : ''}
          />
        </Card>
      </Col>
      <Col flex="0 0 120px">
        <Card size="small" styles={{ body: { padding: 4 } }}>
          {entries.length ? (
            <ReactECharts option={donut} style={{ height: 72 }} />
          ) : (
            <div style={{ height: 72 }} />
          )}
        </Card>
      </Col>
    </Row>
  )
}
