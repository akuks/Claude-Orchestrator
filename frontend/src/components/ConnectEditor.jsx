import { useCallback, useEffect, useState } from 'react'
import { Alert, Button, Collapse, Input, Modal, Popconfirm, Radio, Select, Space, Spin, Tabs, Tag, Typography } from 'antd'
import { CopyOutlined } from '@ant-design/icons'
import { api } from '../api'

const { Paragraph, Text } = Typography

export function relTime(iso, now = Date.now()) {
  if (!iso) return 'never'
  const t = new Date(iso).getTime()
  if (Number.isNaN(t)) return 'never'
  const s = Math.max(0, Math.round((now - t) / 1000))
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)} min ago`
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`
  return `${Math.floor(s / 86400)} days ago`
}
export const day = (iso) => (iso ? String(iso).slice(0, 10) : '')

export const claudeCodeCommand = (url, key) =>
  `claude mcp add --scope user --transport http team-decisions ${url}/mcp --header "Authorization: Bearer ${key}"`
export const cursorSnippet = (url, key) =>
  JSON.stringify({ mcpServers: { 'team-decisions': { url: `${url}/mcp`, headers: { Authorization: `Bearer ${key}` } } } }, null, 2)

const copyText = async (text) => {
  if (!navigator.clipboard?.writeText) throw new Error('no clipboard')
  await navigator.clipboard.writeText(text)
}

// "Connect my editor": personal read-only keys for Claude Code or Cursor on the person's own computer.
export default function ConnectEditor({ open, onClose }) {
  const [info, setInfo] = useState(null) // {enabled, max_keys, days, keys}
  const [projects, setProjects] = useState([])
  const [loading, setLoading] = useState(false)
  const [name, setName] = useState('')
  const [days, setDays] = useState(90)
  const [every, setEvery] = useState(true)
  const [picked, setPicked] = useState([])
  const [creating, setCreating] = useState(false)
  const [created, setCreated] = useState(null) // holds the key text; cleared when the modal closes
  const [error, setError] = useState(null)
  const [copyNote, setCopyNote] = useState(null)

  const copy = async (text) => {
    try {
      await copyText(text)
      setCopyNote('Copied')
    } catch {
      setCopyNote("Couldn't copy — select the key and copy it yourself.")
    }
  }

  const reload = useCallback(async () => {
    const r = await api.myEditorKeys()
    setInfo(r)
  }, [])

  useEffect(() => {
    if (!open) {
      setCreated(null)
      setCopyNote(null)
      setName('')
      setError(null)
      return
    }
    let live = true
    setLoading(true)
    setError(null)
    Promise.all([api.myEditorKeys(), api.listProjects().catch(() => [])])
      .then(([r, ps]) => { if (live) { setInfo(r); setProjects(Array.isArray(ps) ? ps : []) } })
      .catch((e) => { if (live) setError(e.message) })
      .finally(() => { if (live) setLoading(false) })
    return () => { live = false }
  }, [open])

  const create = async () => {
    setCreating(true)
    setError(null)
    try {
      const body = { name: name.trim(), days }
      if (!every) body.projects = picked
      const r = await api.createEditorKey(body)
      setCreated(r)
      setName('')
      await reload()
    } catch (e) {
      setError(e.message)
    } finally {
      setCreating(false)
    }
  }
  const turnOff = async (id) => {
    setError(null)
    try {
      await api.revokeEditorKey(id)
      await reload()
    } catch (e) {
      setError(e.message)
    }
  }

  const enabled = info ? info.enabled !== false : true
  const keys = info?.keys || []
  const nameOf = (k) => (k.project_names?.length ? k.project_names.join(', ') : k.projects?.length ? `${k.projects.length} projects` : 'Every project you can see')
  const canCreate = enabled && name.trim().length > 0 && (every || picked.length > 0)

  return (
    <Modal title="Connect my editor" open={open} onCancel={onClose} footer={<Button onClick={onClose} data-testid="ce-close">Close</Button>} width={680} destroyOnHidden>
      <Spin spinning={loading}>
        {!enabled && <Alert type="info" showIcon style={{ marginBottom: 12 }} message="An admin hasn't turned on editor connections." data-testid="ce-off" />}
        <Paragraph type="secondary">
          Let Claude Code or Cursor on your own computer read your team's decisions. Read-only. It sees only projects you can see.
        </Paragraph>

        {enabled && (
          <div style={{ marginBottom: 16 }}>
            <Text strong>New key</Text>
            <Space direction="vertical" style={{ width: '100%', marginTop: 8 }} size={8}>
              <Input placeholder="Name, for example My laptop" maxLength={60} value={name} onChange={(e) => setName(e.target.value)} data-testid="ce-name" aria-label="Key name" />
              <Space wrap>
                <Text>How long</Text>
                <Radio.Group value={days} onChange={(e) => setDays(e.target.value)} data-testid="ce-days">
                  <Radio.Button value={30}>30 days</Radio.Button>
                  <Radio.Button value={90}>90 days</Radio.Button>
                  <Radio.Button value={365}>365 days</Radio.Button>
                </Radio.Group>
              </Space>
              <Radio.Group value={every ? 'all' : 'pick'} onChange={(e) => setEvery(e.target.value === 'all')} data-testid="ce-scope">
                <Radio value="all">Every project I can see</Radio>
                <Radio value="pick">Only some projects</Radio>
              </Radio.Group>
              {!every && (
                <Select
                  mode="multiple"
                  style={{ width: '100%' }}
                  placeholder="Pick projects"
                  value={picked}
                  onChange={setPicked}
                  options={projects.map((p) => ({ value: p.id, label: p.name }))}
                  data-testid="ce-projects"
                  aria-label="Projects"
                />
              )}
              <Button type="primary" loading={creating} disabled={!canCreate} onClick={create} data-testid="ce-create">Create key</Button>
            </Space>
          </div>
        )}

        {created && (
          <Alert
            type="success"
            style={{ marginBottom: 16 }}
            data-testid="ce-created"
            message={`Key "${created.name}" is ready`}
            description={
              <Space direction="vertical" style={{ width: '100%' }}>
                <Space.Compact style={{ width: '100%' }}>
                  <Input readOnly value={created.key} data-testid="ce-key" aria-label="Your new key" />
                  <Button icon={<CopyOutlined />} onClick={() => copy(created.key)} data-testid="ce-copy">Copy</Button>
                </Space.Compact>
                {copyNote && <Text data-testid="ce-copy-note">{copyNote}</Text>}
                <Text type="warning">You won't see this key again.</Text>
                <Tabs
                  size="small"
                  items={[
                    {
                      key: 'claude',
                      label: 'Claude Code',
                      children: (
                        <>
                          <pre style={{ whiteSpace: 'pre-wrap', margin: 0 }} data-testid="ce-claude">{claudeCodeCommand(created.url, created.key)}</pre>
                          <Button size="small" icon={<CopyOutlined />} onClick={() => copy(claudeCodeCommand(created.url, created.key))}>Copy</Button>
                        </>
                      ),
                    },
                    {
                      key: 'cursor',
                      label: 'Cursor',
                      children: (
                        <>
                          <Paragraph type="secondary" style={{ marginBottom: 4 }}>Add this to your Cursor mcp.json.</Paragraph>
                          <pre style={{ whiteSpace: 'pre-wrap', margin: 0 }} data-testid="ce-cursor">{cursorSnippet(created.url, created.key)}</pre>
                          <Button size="small" icon={<CopyOutlined />} onClick={() => copy(cursorSnippet(created.url, created.key))}>Copy</Button>
                        </>
                      ),
                    },
                  ]}
                />
              </Space>
            }
          />
        )}

        <Text strong>Your keys</Text>
        <div style={{ marginTop: 8 }} data-testid="ce-list">
          {keys.length === 0 && <Text type="secondary">No keys yet.</Text>}
          {keys.map((k) => {
            const off = !!k.revoked_at
            return (
              <div key={k.id} data-testid={`ce-key-${k.id}`} style={{ padding: '8px 0', borderBottom: '1px solid var(--co-border, #eee)' }}>
                <Space wrap>
                  <Text strong>{k.name}</Text>
                  <Text type="secondary">starts with {k.prefix}…</Text>
                  {off && <Tag>Turned off</Tag>}
                  {!off && k.expired && <Tag color="orange">Expired</Tag>}
                </Space>
                <div>
                  <Text type="secondary">
                    Last used {relTime(k.last_used_at)} · Expires {day(k.expires_at)} · {nameOf(k)}
                  </Text>
                </div>
                {!off && (
                  <Popconfirm title="Turn off this key? Editors using it stop working right away." okText="Turn off" onConfirm={() => turnOff(k.id)}>
                    <Button size="small" danger data-testid={`ce-off-${k.id}`}>Turn off</Button>
                  </Popconfirm>
                )}
              </div>
            )
          })}
        </div>
        {error && <Alert style={{ marginTop: 12 }} type="error" showIcon message={error} data-testid="ce-error" />}
        <Collapse
          ghost
          style={{ marginTop: 8 }}
          items={[{
            key: 'd',
            label: 'Details',
            children: <Text type="secondary">At most {info?.max_keys ?? 5} active keys each. Up to 60 requests a minute per key. Keys can read decisions only.</Text>,
          }]}
        />
      </Spin>
    </Modal>
  )
}
