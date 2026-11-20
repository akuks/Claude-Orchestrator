import { useCallback, useEffect, useState } from 'react'
import {
  Alert,
  Button,
  Divider,
  Form,
  Input,
  Modal,
  Popconfirm,
  Radio,
  Space,
  Spin,
  Tag,
  Typography,
  message,
} from 'antd'
import { ApiOutlined, DeleteOutlined, SaveOutlined } from '@ant-design/icons'
import { api } from '../api'

const { Paragraph, Text, Title } = Typography

// Status tag for the caller's Claude credential (presence/kind only — the API
// never returns a secret, and neither does this component display one).
export function claudeStatusTag(st) {
  if (!st) return null
  if (st.claude === 'own')
    return <Tag color="green">{st.claude_kind === 'api_key' ? 'Your Anthropic API key' : 'Your Claude token'}</Tag>
  if (st.claude === 'fallback') return <Tag color="blue">Using the org fallback key</Tag>
  return <Tag color="red">Not set up</Tag>
}

function TestResult({ result }) {
  if (!result) return null
  return (
    <Alert
      style={{ marginTop: 8 }}
      showIcon
      type={result.ok ? 'success' : 'error'}
      message={result.ok ? 'Connection OK' : 'Connection failed'}
      description={result.detail || undefined}
    />
  )
}

// "My credentials": the signed-in user's own Claude credential, GitHub token and
// git identity. Secret inputs are write-only: never pre-filled, cleared after
// every save, and the current value is only ever shown as a status tag.
export default function MyCredentials({ open, onClose, onChanged }) {
  const [status, setStatus] = useState(null)
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState(null) // which action is in flight
  const [claudeKind, setClaudeKind] = useState('oauth_token')
  const [claudeToken, setClaudeToken] = useState('')
  const [githubToken, setGithubToken] = useState('')
  const [tests, setTests] = useState({}) // {claude?: {ok, detail}, github?: ...}
  const [identity] = Form.useForm()

  const apply = useCallback(
    (st) => {
      setStatus(st)
      identity.setFieldsValue({ git_name: st.git_name || '', git_email: st.git_email || '' })
      onChanged?.(st)
    },
    [identity, onChanged]
  )

  useEffect(() => {
    if (!open) return
    // Fresh, empty secret inputs every time the dialog opens.
    setClaudeToken('')
    setGithubToken('')
    setTests({})
    setLoading(true)
    api
      .myCredentials()
      .then((st) => {
        apply(st)
        if (st.claude_kind) setClaudeKind(st.claude_kind)
      })
      .catch((e) => message.error(e.message))
      .finally(() => setLoading(false))
  }, [open]) // eslint-disable-line react-hooks/exhaustive-deps

  const run = async (key, fn, ok) => {
    setBusy(key)
    try {
      apply(await fn())
      if (ok) message.success(ok)
      return true
    } catch (e) {
      message.error(e.message)
      return false
    } finally {
      setBusy(null)
    }
  }

  const saveClaude = async () => {
    const token = claudeToken.trim()
    if (!token) return
    // Clear the input whether or not the save succeeds: it never lingers.
    setClaudeToken('')
    setTests((t) => ({ ...t, claude: undefined }))
    await run('claude', () => api.saveMyCredentials({ claude_token: token, claude_kind: claudeKind }), 'Claude credential saved')
  }
  const saveGithub = async () => {
    const token = githubToken.trim()
    if (!token) return
    setGithubToken('')
    setTests((t) => ({ ...t, github: undefined }))
    await run('github', () => api.saveMyCredentials({ github_token: token }), 'GitHub token saved')
  }
  const saveIdentity = async () => {
    const v = await identity.validateFields().catch(() => null)
    if (!v) return
    await run(
      'identity',
      () => api.saveMyCredentials({ git_name: v.git_name?.trim() || null, git_email: v.git_email?.trim() || null }),
      'Git identity saved'
    )
  }
  const test = async (which) => {
    setBusy(`test-${which}`)
    setTests((t) => ({ ...t, [which]: undefined }))
    try {
      const r = await api.testMyCredential(which)
      setTests((t) => ({ ...t, [which]: r }))
    } catch (e) {
      message.error(e.message)
    } finally {
      setBusy(null)
    }
  }

  const hasOwnClaude = status?.claude === 'own'

  return (
    <Modal
      title="My credentials"
      open={open}
      onCancel={onClose}
      footer={<Button onClick={onClose}>Close</Button>}
      width={640}
      destroyOnHidden
    >
      <Spin spinning={loading}>
        <Paragraph type="secondary">
          Tasks you start run on your own Claude account and carry your git identity. Saved
          secrets are encrypted and can't be viewed again — only replaced or removed.
        </Paragraph>

        <Title level={5} style={{ marginTop: 8 }}>
          Claude <span data-testid="claude-status">{claudeStatusTag(status)}</span>
        </Title>
        <Paragraph type="secondary" style={{ marginBottom: 8 }}>
          On your own machine run <Text code>claude setup-token</Text>, sign in, and paste the token
          it prints (kind <i>Claude token</i>). Or paste an Anthropic API key from console.anthropic.com
          (kind <i>API key</i>). Saving replaces whichever one you had before.
          {status?.claude === 'fallback' && ' Until you add one, your tasks use the org fallback key.'}
        </Paragraph>
        <Space direction="vertical" style={{ width: '100%' }}>
          <Radio.Group
            value={claudeKind}
            onChange={(e) => setClaudeKind(e.target.value)}
            options={[
              { value: 'oauth_token', label: 'Claude token (claude setup-token)' },
              { value: 'api_key', label: 'Anthropic API key' },
            ]}
          />
          <Space.Compact style={{ width: '100%' }}>
            <Input.Password
              data-testid="claude-token-input"
              autoComplete="new-password"
              placeholder={hasOwnClaude ? 'Paste a new value to replace the saved one' : 'Paste your token or key'}
              value={claudeToken}
              onChange={(e) => setClaudeToken(e.target.value)}
              onPressEnter={saveClaude}
              visibilityToggle={false}
            />
            <Button
              type="primary"
              icon={<SaveOutlined />}
              loading={busy === 'claude'}
              disabled={!claudeToken.trim()}
              onClick={saveClaude}
            >
              {hasOwnClaude ? 'Replace' : 'Save'}
            </Button>
          </Space.Compact>
          <Space>
            <Button loading={busy === 'test-claude'} disabled={status?.claude === 'none'} onClick={() => test('claude')} icon={<ApiOutlined />}>
              Test connection
            </Button>
            {hasOwnClaude && (
              <Popconfirm title="Remove your Claude credential?" onConfirm={() => run('rm-claude', api.removeMyClaude, 'Claude credential removed')}>
                <Button danger icon={<DeleteOutlined />} loading={busy === 'rm-claude'}>
                  Remove
                </Button>
              </Popconfirm>
            )}
          </Space>
          <TestResult result={tests.claude} />
        </Space>

        <Divider />

        <Title level={5}>
          GitHub{' '}
          <span data-testid="github-status">
            {status && (status.github ? <Tag color="green">Token set</Tag> : <Tag>Not set</Tag>)}
          </span>
        </Title>
        <Paragraph type="secondary" style={{ marginBottom: 8 }}>
          A fine-grained personal access token with access to the repos you work on (Contents and
          Pull requests read/write). It's used for pushes and pull requests as you in an upcoming
          release; your git name and email below are used for commits now.
        </Paragraph>
        <Space direction="vertical" style={{ width: '100%' }}>
          <Space.Compact style={{ width: '100%' }}>
            <Input.Password
              data-testid="github-token-input"
              autoComplete="new-password"
              placeholder={status?.github ? 'Paste a new token to replace the saved one' : 'github_pat_…'}
              value={githubToken}
              onChange={(e) => setGithubToken(e.target.value)}
              onPressEnter={saveGithub}
              visibilityToggle={false}
            />
            <Button
              type="primary"
              icon={<SaveOutlined />}
              loading={busy === 'github'}
              disabled={!githubToken.trim()}
              onClick={saveGithub}
            >
              {status?.github ? 'Replace' : 'Save'}
            </Button>
          </Space.Compact>
          <Space>
            <Button loading={busy === 'test-github'} disabled={!status?.github} onClick={() => test('github')} icon={<ApiOutlined />}>
              Test connection
            </Button>
            {status?.github && (
              <Popconfirm title="Remove your GitHub token?" onConfirm={() => run('rm-github', api.removeMyGithub, 'GitHub token removed')}>
                <Button danger icon={<DeleteOutlined />} loading={busy === 'rm-github'}>
                  Remove
                </Button>
              </Popconfirm>
            )}
          </Space>
          <TestResult result={tests.github} />
        </Space>

        <Title level={5} style={{ marginTop: 20 }}>
          Git identity
        </Title>
        <Form form={identity} layout="vertical" requiredMark={false}>
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
            <Form.Item name="git_name" label="Name" style={{ flex: 1, minWidth: 200 }}>
              <Input placeholder="Ada Lovelace" maxLength={200} />
            </Form.Item>
            <Form.Item
              name="git_email"
              label="Email"
              style={{ flex: 1, minWidth: 200 }}
              rules={[{ type: 'email', message: 'Enter an email address' }]}
            >
              <Input placeholder="ada@example.com" maxLength={320} />
            </Form.Item>
          </div>
          <Button icon={<SaveOutlined />} loading={busy === 'identity'} onClick={saveIdentity}>
            Save identity
          </Button>
        </Form>
      </Spin>
    </Modal>
  )
}
