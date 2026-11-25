import { useEffect, useState } from 'react'
import { Card, Collapse, Typography } from 'antd'
import { api } from '../api'
import dayjs from 'dayjs'
import relativeTime from 'dayjs/plugin/relativeTime'
import { when } from '../time'
import { localWhen, STATUS_WORDS, startsAgainText } from './plan'

dayjs.extend(relativeTime)

// Usage page: the Claude plan in plain words; raw figures only under Details.
export default function PlanCard() {
  const [data, setData] = useState(null)
  useEffect(() => {
    api.planStatus().then(setData).catch(() => setData(null))
  }, [])
  if (!data) return null
  const st = data.status
  let body
  if (data.plan_applies === false && data.api_key) {
    body = <Typography.Text>Plan limits don't apply to API keys.</Typography.Text>
  } else if (!st) {
    body = <Typography.Text>Nothing reported yet. It appears after the next task runs.</Typography.Text>
  } else {
    const word = st.held ? STATUS_WORDS.rejected : STATUS_WORDS[st.status] || 'Not known (see Details)'
    const reset = localWhen(st.resets_at)
    body = (
      <>
        <div data-testid="plan-status-word" style={{ fontSize: 16, fontWeight: 600 }}>{word}</div>
        {reset && <div>Resets at {reset}</div>}
        {st.held && startsAgainText(st) && <div data-testid="plan-starts-again">{startsAgainText(st)}</div>}
        {st.seen_at && (
          <Typography.Text type="secondary">Last checked {when(st.seen_at).fromNow()}</Typography.Text>
        )}
        <Collapse
          ghost
          size="small"
          items={[
            {
              key: 'details',
              label: 'Details',
              children: (
                <div style={{ fontSize: 12 }}>
                  <div>Utilization: {st.utilization ?? 'not reported'}</div>
                  <pre style={{ whiteSpace: 'pre-wrap' }}>{typeof st.raw === 'string' ? st.raw : JSON.stringify(st.raw, null, 2)}</pre>
                </div>
              ),
            },
          ]}
        />
      </>
    )
  }
  return (
    <Card size="small" title="Claude plan" style={{ marginBottom: 16 }} data-testid="plan-card">
      {body}
    </Card>
  )
}
