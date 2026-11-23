import { useState } from 'react'
import { Alert, Button, Collapse, Input, Space, Typography } from 'antd'
import { api } from '../api'

// Problems by line. Everything is rendered as text (never as HTML).
export function Problems({ problems }) {
  if (!problems?.length) return <Alert type="success" showIcon message="No problems" data-testid="lint-ok" />
  return (
    <ul data-testid="lint-problems" style={{ margin: 0, paddingLeft: 18 }}>
      {problems.map((p, i) => (
        <li key={i} data-testid="lint-problem">
          <b>{`Line ${p.line}`}</b>
          {': '}
          {String(p.message)}
        </li>
      ))}
    </ul>
  )
}

// Results of GET /skills/{id}/lint: [{file, problems}].
export function PackResults({ results }) {
  if (!results) return null
  if (!results.length) return <Typography.Text type="secondary">No SKILL.md files in this pack.</Typography.Text>
  return (
    <Space direction="vertical" style={{ width: '100%' }}>
      {results.map((r) => (
        <div key={r.file} data-testid="pack-lint-file">
          <Typography.Text code>{String(r.file)}</Typography.Text>
          <Problems problems={r.problems} />
        </div>
      ))}
    </Space>
  )
}

export default function SkillLint() {
  const [text, setText] = useState('')
  const [template, setTemplate] = useState(null)
  const [result, setResult] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const loadTemplate = async () => {
    setError('')
    try {
      const t = await api.skillTemplate()
      setTemplate(t.text)
    } catch (e) {
      setError(e.message)
    }
  }

  const run = async () => {
    setBusy(true)
    setError('')
    try {
      setResult(await api.lintSkillText(text))
    } catch (e) {
      setResult(null)
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Collapse
      style={{ marginBottom: 12 }}
      items={[
        {
          key: 'lint',
          label: 'Skill template & lint',
          children: (
            <Space direction="vertical" style={{ width: '100%' }}>
              <Typography.Text type="secondary">
                Lint is advice only: nothing is blocked or saved, and the server writes nothing into project directories.
              </Typography.Text>
              <Space>
                <Button size="small" onClick={loadTemplate} data-testid="show-template">Show template</Button>
                {template != null && (
                  <Button size="small" onClick={() => setText(template)} data-testid="use-template">Use as starting text</Button>
                )}
              </Space>
              {template != null && (
                <pre data-testid="template-text" style={{ maxHeight: 240, overflow: 'auto', margin: 0, fontSize: 12 }}>{template}</pre>
              )}
              <Input.TextArea
                rows={10}
                value={text}
                onChange={(e) => setText(e.target.value)}
                placeholder="Paste a SKILL.md here"
                data-testid="lint-input"
              />
              <Button type="primary" loading={busy} onClick={run} data-testid="lint-run">Lint</Button>
              {error && <Alert type="error" showIcon message={error} data-testid="lint-error" />}
              {result && <Problems problems={result.problems} />}
            </Space>
          ),
        },
      ]}
    />
  )
}
