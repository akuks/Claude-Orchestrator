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
import PlanCard from '../plan/PlanCard'
import { modeTokens, palette } from '../theme/tokens'
import { useThemeMode } from '../theme/ThemeModeContext.jsx'

// Categorical series colours: brand sunset first, then status hues.
const SERIES_COLORS = [
  palette.orange,
  palette.rose,
  palette.amber,
  palette.status.info,
  palette.status.success,
]

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
  const { mode } = useThemeMode()
  const t = modeTokens(mode)
  const tooltipStyle = {
    backgroundColor: t.elevated,
    borderColor: t.border,
    textStyle: { color: t.text },
  }
  const axisStyle = {
    axisLine: { lineStyle: { color: t.border } },
    axisTick: { lineStyle: { color: t.border } },
    splitLine: { lineStyle: { color: t.borderSecondary } },
  }
  const [summary, setSummary] = useState(null)
  const [series, setSeries] = useState([])
  const [byProject, setByProject] = useState([])
  const [byModel, setByModel] = useState([])
  const [byUser, setByUser] = useState([])

  const refresh = useCallback(async () => {
    try {
      const [s, ts, bp, bm, bu] = await Promise.all([
        api.usageSummary(),
        api.usageTimeseries(30),
        api.usageByProject(),
        api.usageByModel(),
        api.usageByUser(),
      ])
      setSummary(s)
      setSeries(ts)
      setByProject(bp)
      setByModel(bm)
      setByUser(bu)
    } catch (e) {
      message.error(e.message)
    }
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  const costChart = {
    backgroundColor: 'transparent',
    textStyle: { fontFamily: 'Inter, system-ui, sans-serif' },
    tooltip: { trigger: 'axis', ...tooltipStyle },
    grid: { left: 50, right: 20, top: 20, bottom: 30 },
    xAxis: {
      type: 'category',
      data: series.map((d) => d.date),
      axisLabel: { color: t.textSecondary },
      ...axisStyle,
    },
    yAxis: {
      type: 'value',
      axisLabel: { formatter: '${value}', color: t.textSecondary },
      ...axisStyle,
    },
    series: [
      {
        type: 'bar',
        data: series.map((d) => d.cost_usd),
        itemStyle: { color: palette.primary, borderRadius: [4, 4, 0, 0] },
        name: 'Daily cost',
      },
    ],
  }

  const modelPie = {
    backgroundColor: 'transparent',
    color: SERIES_COLORS,
    tooltip: { trigger: 'item', formatter: '{b}: ${c} ({d}%)', ...tooltipStyle },
    series: [
      {
        type: 'pie',
        radius: ['45%', '75%'],
        data: byModel.map((m) => ({ name: m.model, value: m.cost_usd })),
        label: { fontSize: 11, color: t.textSecondary },
        itemStyle: { borderColor: t.surfaceSolid, borderWidth: 1 },
      },
    ],
  }

  if (!summary) return <Empty description="Loading usage…" />

  return (
    <>
      <PlanCard />
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

      <Card size="small" title="Cost by user" style={{ marginTop: 16 }}>
        <Table
          rowKey={(r) => r.user_id || '(unassigned)'}
          size="small"
          pagination={false}
          dataSource={byUser}
          locale={{ emptyText: 'No cost data yet' }}
          columns={[
            {
              title: 'User',
              dataIndex: 'name',
              render: (n, row) =>
                row.user_id ? (
                  <span>
                    <b>{n}</b>{' '}
                    {row.email && row.email !== n && (
                      <Typography.Text type="secondary">{row.email}</Typography.Text>
                    )}
                  </span>
                ) : (
                  <Typography.Text type="secondary">{n}</Typography.Text>
                ),
            },
            {
              title: 'Cost',
              dataIndex: 'cost_usd',
              width: 120,
              render: (c) => money(c),
              sorter: (a, b) => a.cost_usd - b.cost_usd,
              defaultSortOrder: 'descend',
            },
            { title: 'Tokens', dataIndex: 'tokens', width: 100, render: (t) => compact(t) },
            { title: 'Tasks', dataIndex: 'tasks', width: 80 },
          ]}
        />
      </Card>
    </>
  )
}
