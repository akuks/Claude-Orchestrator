import { useCallback, useEffect, useState } from 'react'
import { Button, Card, Input, Popconfirm, Space, Tag, Typography, message } from 'antd'
import { DeleteOutlined, SaveOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import { api } from '../api'

// Admin: the org fallback Anthropic API key, used by people who haven't added
// their own Claude credential. Write-only — only "set / not set" is ever shown,
// and the input is never pre-filled and is cleared after each save.
export default function OrgFallbackCard({ onChanged }) {
  const [info, setInfo] = useState(null)
  const [key, setKey] = useState('')
  const [busy, setBusy] = useState(null)

  const load = useCallback(async () => {
    try {
      setInfo(await api.orgFallback())
    } catch (e) {
      message.error(e.message)
    }
  }, [])
  useEffect(() => {
    load()
  }, [load])

  const act = async (which, fn, ok) => {
    setBusy(which)
    try {
      setInfo(await fn())
      message.success(ok)
      onChanged?.()
    } catch (e) {
      message.error(e.message)
    } finally {
      setBusy(null)
    }
  }

  const save = () => {
    const v = key.trim()
    if (!v) return
    setKey('')
    act('save', () => api.setOrgFallback(v), 'Org fallback key saved')
  }

  return (
    <Card
      size="small"
      style={{ marginBottom: 16 }}
      title={
        <Space>
          Org fallback Claude key
          <span data-testid="fallback-status">
            {info && (info.set ? <Tag color="green">Set</Tag> : <Tag>Not set</Tag>)}
          </span>
        </Space>
      }
    >
      <Typography.Paragraph type="secondary" style={{ marginBottom: 8 }}>
        An Anthropic API key that tasks use for anyone without their own Claude credential. Off
        unless set — without it, those people's tasks refuse to run until they add credentials.
        {info?.set && (
          <>
            {' '}
            Last updated {info.updated_at ? dayjs(info.updated_at).fromNow() : ''}
            {info.updated_by ? ` by ${info.updated_by}` : ''}.
          </>
        )}
      </Typography.Paragraph>
      <Space wrap>
        <Space.Compact style={{ width: 420, maxWidth: '100%' }}>
          <Input.Password
            data-testid="fallback-input"
            autoComplete="new-password"
            placeholder={info?.set ? 'Paste a new key to replace the saved one' : 'sk-ant-…'}
            value={key}
            onChange={(e) => setKey(e.target.value)}
            onPressEnter={save}
            visibilityToggle={false}
          />
          <Button type="primary" icon={<SaveOutlined />} loading={busy === 'save'} disabled={!key.trim()} onClick={save}>
            {info?.set ? 'Replace' : 'Save'}
          </Button>
        </Space.Compact>
        {info?.set && (
          <Popconfirm
            title="Remove the org fallback key? People without their own credential won't be able to run tasks."
            onConfirm={() => act('rm', api.removeOrgFallback, 'Org fallback key removed')}
          >
            <Button danger icon={<DeleteOutlined />} loading={busy === 'rm'}>
              Remove
            </Button>
          </Popconfirm>
        )}
      </Space>
    </Card>
  )
}
