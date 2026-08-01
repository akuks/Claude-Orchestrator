import { useCallback, useEffect, useState } from 'react'
import {
  Card,
  Col,
  Empty,
  Row,
  Statistic,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import ReactECharts from 'echarts-for-react'
import { api } from '../api'

const money = (v) => `$${(v || 0).toFixed(2)}`
const compact = (n) =>
  n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${(n / 1e3).toFixed(1)}k` : `${n || 0}`

function SummaryCards({ s }) {
  const windows = [
    ['Today', s.today],
    ['Last 7 days', s.last_7d],
    ['Last 30 days', s.last_30d],
    ['All time', s.all_time],
  ]
  return (
    <Row gutter={12} style={{ marginBottom: 16 }}>
      {windows.map(([label, w]) => (
        <Col flex="1" key={label}>
          <Card size="small">
            <Statistic title={`${label} — cost`} value={money(w?.cost_usd)} />
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {w?.tasks || 0} tasks · {compact(w?.tokens || 0)} tokens
            </Typography.Text>
          </Card>
        </Col>
      ))}
    </Row>
  )
}

export default function UsageView() {
  const [summary, setSummary] = useState(null)
  const [series, setSeries] = useState([])
  const [byProject, setByProject] = useState([])
  const [byModel, setByModel] = useState([])

  const refresh = useCallback(async () => {
    try {
      const [s, ts, bp, bm] = await Promise.all([
        api.usageSummary(),
        api.usageTimeseries(30),
        api.usageByProject(),
        api.usageByModel(),
      ])
      setSummary(s)
      setSeries(ts)
      setByProject(bp)
      setByModel(bm)
    } catch (e) {
      message.error(e.message)
    }
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  const costChart = {
    tooltip: { trigger: 'axis' },
    grid: { left: 50, right: 20, top: 20, bottom: 30 },
    xAxis: { type: 'category', data: series.map((d) => d.date) },
    yAxis: { type: 'value', axisLabel: { formatter: '${value}' } },
    series: [
      {
        type: 'bar',
        data: series.map((d) => d.cost_usd),
        itemStyle: { color: '#d97757' },
        name: 'Daily cost',
      },
    ],
  }

  const modelPie = {
    tooltip: { trigger: 'item', formatter: '{b}: ${c} ({d}%)' },
    series: [
      {
        type: 'pie',
        radius: ['45%', '75%'],
        data: byModel.map((m) => ({ name: m.model, value: m.cost_usd })),
        label: { fontSize: 11 },
      },
    ],
  }

  if (!summary) return <Empty description="Loading usage…" />

  return (
    <>
      <SummaryCards s={summary} />

      <Row gutter={12} style={{ marginBottom: 16 }}>
        <Col span={16}>
          <Card size="small" title="Daily cost (last 30 days)">
            {series.length ? (
              <ReactECharts option={costChart} style={{ height: 260 }} />
            ) : (
              <Empty description="No cost data yet" />
            )}
          </Card>
        </Col>
        <Col span={8}>
          <Card size="small" title="Cost by model">
            {byModel.length ? (
              <ReactECharts option={modelPie} style={{ height: 260 }} />
            ) : (
              <Empty description="No data" />
            )}
          </Card>
        </Col>
      </Row>

      <Card size="small" title="Cost by project">
        <Table
          rowKey="project"
          size="small"
          pagination={false}
          dataSource={byProject}
          columns={[
            { title: 'Project', dataIndex: 'project', render: (p) => <b>{p}</b> },
            {
              title: 'Cost',
              dataIndex: 'cost_usd',
              width: 120,
              render: (c) => money(c),
              sorter: (a, b) => a.cost_usd - b.cost_usd,
            },
            {
              title: 'Budget',
              dataIndex: 'budget_usd',
              width: 200,
              render: (b, row) =>
                b ? (
                  <span>
                    {money(row.cost_usd)} / {money(b)}{' '}
                    {row.over_budget ? (
                      <Tag color="red">over budget</Tag>
                    ) : (
                      <Tag color="green">{Math.round((row.cost_usd / b) * 100)}%</Tag>
                    )}
                  </span>
                ) : (
                  <Typography.Text type="secondary">—</Typography.Text>
                ),
            },
            { title: 'Tokens', dataIndex: 'tokens', width: 100, render: (t) => compact(t) },
            { title: 'Tasks', dataIndex: 'tasks', width: 80 },
          ]}
        />
      </Card>
    </>
  )
}
