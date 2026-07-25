import { useEffect, useState } from 'react'
import {
  Alert,
  Button,
  Form,
  Input,
  Modal,
  Select,
  Switch,
  message,
} from 'antd'
import { api } from '../api'

// key=value textarea <-> object helpers for env / headers entry.
function objToText(obj) {
  return Object.entries(obj || {})
    .map(([k, v]) => `${k}=${v}`)
    .join('\n')
}
function textToObj(text) {
  const out = {}
  for (const line of (text || '').split('\n')) {
    const t = line.trim()
    if (!t) continue
    const i = t.indexOf('=')
    if (i === -1) continue
    out[t.slice(0, i).trim()] = t.slice(i + 1).trim()
  }
  return out
}

export default function McpServerModal({ open, server, onClose, onSaved }) {
  const [form] = Form.useForm()
  const [transport, setTransport] = useState('stdio')
  const [saving, setSaving] = useState(false)
  const isEdit = !!server

  useEffect(() => {
    if (!open) return
    if (server) {
      setTransport(server.transport)
      form.setFieldsValue({
        name: server.name,
        transport: server.transport,
        scope: server.scope,
        project: server.project,
        command: server.command,
        args: (server.args || []).join(' '),
        url: server.url,
        enabled: server.enabled,
        env: '', // secrets are never returned; leave blank unless changing
        headers: '',
      })
    } else {
      form.resetFields()
      setTransport('stdio')
      form.setFieldsValue({ transport: 'stdio', scope: 'team', enabled: true })
    }
  }, [open, server])

  const submit = async () => {
    let v
    try {
      v = await form.validateFields()
    } catch {
      return
    }
    setSaving(true)
    try {
      const args = (v.args || '').trim() ? v.args.trim().split(/\s+/) : []
      if (isEdit) {
        const patch = {
          scope: v.scope,
          project: v.scope === 'project' ? v.project : null,
          command: v.command,
          args,
          url: v.url,
          enabled: v.enabled,
        }
        if (v.env?.trim()) patch.env = textToObj(v.env)
        if (v.headers?.trim()) patch.headers = textToObj(v.headers)
        await api.updateServer(server.id, patch)
      } else {
        await api.createServer({
          name: v.name,
          transport: v.transport,
          scope: v.scope,
          project: v.scope === 'project' ? v.project : null,
          command: v.command,
          args,
          url: v.url,
          env: textToObj(v.env),
          headers: textToObj(v.headers),
          enabled: v.enabled,
        })
      }
      message.success(isEdit ? 'Server updated' : 'Server added')
      onSaved?.()
      onClose()
    } catch (e) {
      message.error(e.message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal
      title={isEdit ? `Edit ${server.name}` : 'Add MCP Server'}
      open={open}
      onCancel={onClose}
      width={600}
      footer={[
        <Button key="c" onClick={onClose}>
          Cancel
        </Button>,
        <Button key="s" type="primary" loading={saving} onClick={submit}>
          {isEdit ? 'Save' : 'Add'}
        </Button>,
      ]}
    >
      <Form form={form} layout="vertical">
        <div style={{ display: 'flex', gap: 12 }}>
          <Form.Item
            name="name"
            label="Name"
            style={{ flex: 1 }}
            rules={[{ required: true }]}
          >
            <Input placeholder="e.g. github" disabled={isEdit} />
          </Form.Item>
          <Form.Item name="transport" label="Transport" style={{ width: 140 }}>
            <Select
              disabled={isEdit}
              onChange={setTransport}
              options={[
                { value: 'stdio', label: 'stdio' },
                { value: 'http', label: 'HTTP' },
              ]}
            />
          </Form.Item>
        </div>

        <div style={{ display: 'flex', gap: 12 }}>
          <Form.Item name="scope" label="Scope" style={{ width: 160 }}>
            <Select
              options={[
                { value: 'team', label: 'Team-shared' },
                { value: 'user', label: 'Per-user' },
                { value: 'project', label: 'Per-project' },
              ]}
            />
          </Form.Item>
          <Form.Item
            noStyle
            shouldUpdate={(p, c) => p.scope !== c.scope}
          >
            {({ getFieldValue }) =>
              getFieldValue('scope') === 'project' ? (
                <Form.Item
                  name="project"
                  label="Project"
                  style={{ flex: 1 }}
                  rules={[{ required: true }]}
                >
                  <Input placeholder="project name" />
                </Form.Item>
              ) : null
            }
          </Form.Item>
        </div>

        {transport === 'stdio' ? (
          <>
            <Form.Item
              name="command"
              label="Command"
              rules={[{ required: true }]}
            >
              <Input placeholder="e.g. npx" />
            </Form.Item>
            <Form.Item name="args" label="Args (space-separated)">
              <Input placeholder="-y @modelcontextprotocol/server-github" />
            </Form.Item>
            <Form.Item
              name="env"
              label={
                isEdit
                  ? 'Environment (key=value per line — leave blank to keep existing)'
                  : 'Environment (key=value per line)'
              }
            >
              <Input.TextArea rows={3} placeholder="GITHUB_TOKEN=ghp_..." />
            </Form.Item>
          </>
        ) : (
          <>
            <Form.Item name="url" label="URL" rules={[{ required: true }]}>
              <Input placeholder="https://mcp.example.com/sse" />
            </Form.Item>
            <Form.Item
              name="headers"
              label={
                isEdit
                  ? 'Headers (key=value per line — leave blank to keep existing)'
                  : 'Headers (key=value per line)'
              }
            >
              <Input.TextArea rows={3} placeholder="Authorization=Bearer ..." />
            </Form.Item>
            <Alert
              type="info"
              showIcon
              message="HTTP transport health checks aren't validated in Phase 2 (config is still injected into tasks)."
              style={{ marginBottom: 12 }}
            />
          </>
        )}

        <Form.Item name="enabled" label="Enabled" valuePropName="checked">
          <Switch />
        </Form.Item>
      </Form>
    </Modal>
  )
}
