import { useState } from 'react'
import { Alert, Button, Card, Form, Input, Typography } from 'antd'
import { api } from '../api'

export default function SetupPage({ onDone }) {
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  const submit = async (v) => {
    setBusy(true)
    setError(null)
    try {
      onDone(await api.setup({ email: v.email, name: v.name, password: v.password }))
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div style={{ display: 'flex', justifyContent: 'center', paddingTop: 100 }}>
      <Card style={{ width: 420 }}>
        <Typography.Title level={4} style={{ marginTop: 0 }}>
          🪄 Welcome — create the first admin
        </Typography.Title>
        <Typography.Paragraph type="secondary">
          No users exist yet. This account will be the administrator.
        </Typography.Paragraph>
        {error && <Alert type="error" showIcon message={error} style={{ marginBottom: 16 }} />}
        <Form layout="vertical" onFinish={submit}>
          <Form.Item name="name" label="Name" rules={[{ required: true, message: 'Enter a name' }]}>
            <Input autoFocus />
          </Form.Item>
          <Form.Item
            name="email"
            label="Email"
            rules={[{ required: true, type: 'email', message: 'Enter a valid email' }]}
          >
            <Input autoComplete="username" />
          </Form.Item>
          <Form.Item
            name="password"
            label="Password"
            rules={[{ required: true, min: 8, message: 'At least 8 characters' }]}
          >
            <Input.Password autoComplete="new-password" />
          </Form.Item>
          <Form.Item
            name="confirm"
            label="Confirm password"
            dependencies={['password']}
            rules={[
              { required: true, message: 'Confirm the password' },
              ({ getFieldValue }) => ({
                validator: (_, val) =>
                  !val || getFieldValue('password') === val
                    ? Promise.resolve()
                    : Promise.reject(new Error('Passwords do not match')),
              }),
            ]}
          >
            <Input.Password autoComplete="new-password" />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={busy} block>
            Create admin and sign in
          </Button>
        </Form>
      </Card>
    </div>
  )
}
