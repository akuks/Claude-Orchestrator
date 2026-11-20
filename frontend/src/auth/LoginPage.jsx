import { useState } from 'react'
import { Alert, Button, Card, Form, Input, Typography } from 'antd'
import { api } from '../api'

export default function LoginPage({ notice, onLoggedIn }) {
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  const submit = async (v) => {
    setBusy(true)
    setError(null)
    try {
      onLoggedIn(await api.login(v.email, v.password))
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div style={{ display: 'flex', justifyContent: 'center', paddingTop: 120 }}>
      <Card style={{ width: 380 }}>
        <Typography.Title level={4} style={{ marginTop: 0 }}>
          🪄 Claude Orchestrator
        </Typography.Title>
        {notice && <Alert type="warning" showIcon message={notice} style={{ marginBottom: 16 }} />}
        {error && <Alert type="error" showIcon message={error} style={{ marginBottom: 16 }} />}
        <Form layout="vertical" onFinish={submit}>
          <Form.Item name="email" label="Email" rules={[{ required: true, message: 'Enter your email' }]}>
            <Input autoFocus autoComplete="username" />
          </Form.Item>
          <Form.Item name="password" label="Password" rules={[{ required: true, message: 'Enter your password' }]}>
            <Input.Password autoComplete="current-password" />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={busy} block>
            Sign in
          </Button>
        </Form>
      </Card>
    </div>
  )
}
