import { useEffect, useState } from 'react'
import { Alert, Button, InputNumber, Slider, Switch, Typography, message } from 'antd'
import { SafetyOutlined } from '@ant-design/icons'
import { api } from '../api'
import { GUARD_SETTINGS_HELP, WAIT_MAX, WAIT_MIN, formatWait } from './guard'
import './guard.css'

// Project drawer → Guard tab: co-guard on/off and how long a paused action
// waits for a person. Maintainers and admins edit; everyone else reads.
export default function GuardSettings({ project, canEdit, onSaved }) {
  const [enabled, setEnabled] = useState(project.guard_enabled !== false)
  const [wait, setWait] = useState(project.approval_wait_seconds || 300)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    setEnabled(project.guard_enabled !== false)
    setWait(project.approval_wait_seconds || 300)
  }, [project.id, project.guard_enabled, project.approval_wait_seconds])

  const dirty = enabled !== (project.guard_enabled !== false) || wait !== (project.approval_wait_seconds || 300)
  const clamp = (v) => Math.min(WAIT_MAX, Math.max(WAIT_MIN, Math.round(Number(v) || WAIT_MIN)))

  const save = async () => {
    setSaving(true)
    try {
      await api.updateProject(project.id, { guard_enabled: enabled, approval_wait_seconds: clamp(wait) })
      message.success('Guard settings saved')
      onSaved?.()
    } catch (e) {
      message.error(e.message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div data-testid="guard-settings">
      <Typography.Paragraph type="secondary" data-testid="guard-help">
        <SafetyOutlined /> {GUARD_SETTINGS_HELP}
      </Typography.Paragraph>

      <div className="co-guard-settings-row">
        <Switch
          checked={enabled}
          disabled={!canEdit}
          onChange={setEnabled}
          data-testid="guard-enabled"
          aria-label="Built-in guard rules"
        />
        <Typography.Text strong>Built-in rules {enabled ? 'on' : 'off'}</Typography.Text>
      </div>
      {!enabled && (
        <Alert
          data-testid="guard-off-warning"
          type="warning"
          showIcon
          style={{ marginBottom: 16 }}
          message="Dangerous shell commands will run without a check"
          description="MCP approvals and blocks, and self-protection, still apply."
        />
      )}

      <Typography.Text strong>Approval wait</Typography.Text>
      <Typography.Paragraph type="secondary" style={{ marginBottom: 8 }}>
        How long a paused action waits for a person before it is blocked:{' '}
        <span className="co-num" data-testid="guard-wait-label">
          {formatWait(clamp(wait))}
        </span>
      </Typography.Paragraph>
      <div className="co-guard-settings-row">
        <Slider
          className="co-guard-settings-slider"
          min={WAIT_MIN}
          max={WAIT_MAX}
          step={30}
          value={clamp(wait)}
          disabled={!canEdit}
          onChange={setWait}
          tooltip={{ formatter: formatWait }}
          marks={{ 60: '1m', 300: '5m', 600: '10m', 900: '15m' }}
          aria-label="Approval wait"
        />
        <span data-testid="guard-wait-input">
          <InputNumber
            min={WAIT_MIN}
            max={WAIT_MAX}
            step={30}
            value={wait}
            disabled={!canEdit}
            onChange={(v) => v != null && setWait(v)}
            onBlur={() => setWait((w) => clamp(w))}
            suffix="s"
            style={{ width: 130 }}
            aria-label="Approval wait in seconds"
          />
        </span>
      </div>

      {canEdit ? (
        <Button type="primary" loading={saving} disabled={!dirty} onClick={save}>
          Save guard settings
        </Button>
      ) : (
        <Typography.Text type="secondary" data-testid="guard-readonly">
          Only project maintainers and admins can change these settings.
        </Typography.Text>
      )}
    </div>
  )
}
