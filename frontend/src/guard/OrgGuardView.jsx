import { useCallback, useEffect, useMemo, useState } from 'react'
import { Alert, Button, Card, Checkbox, Input, Select, Space, Switch, Tag, Tooltip, Typography, message } from 'antd'
import { LockOutlined, SafetyOutlined } from '@ant-design/icons'
import { api } from '../api'
import RuleTable from './RuleTable'
import { DEV_DB_HELP, LOCKED_HELP, RM_RF_DISABLE_HELP, hookErrorsLine, ruleName } from './guard'
import './guard.css'

const sameSet = (a, b) => a.length === b.length && a.every((x) => b.includes(x))

// Built-ins: the switchable ones as checkboxes ("on" = checked), the rest
// locked with why. disabled: the ids switched off.
export function BuiltinList({ builtins, disabled, canEdit, onChange }) {
  const switchable = builtins.filter((b) => b.disableable)
  const locked = builtins.filter((b) => !b.disableable)
  const set = (id, on) => onChange(on ? disabled.filter((x) => x !== id) : [...disabled, id])
  return (
    <div data-testid="builtin-list">
      <div className="co-guard-builtins">
        {switchable.map((b) => {
          const on = !disabled.includes(b.id)
          return (
            <div key={b.id} className="co-guard-builtin" data-testid={`builtin-${b.id}`}>
              <Checkbox checked={on} disabled={!canEdit} onChange={(e) => set(b.id, e.target.checked)}>
                <span className="co-guard-builtin-name">{ruleName(b.id)}</span>{' '}
                <span className="co-guard-mono co-guard-builtin-id">{b.id}</span>
              </Checkbox>
              <div className="co-guard-builtin-why">{b.reason || b.hard_reason}</div>
              {b.id === 'rm-rf-outside' && (
                <div className="co-guard-builtin-warn" data-testid="rm-rf-warning">
                  {RM_RF_DISABLE_HELP}
                </div>
              )}
              {!on && b.hard_reason && b.id !== 'rm-rf-outside' && (
                <div className="co-guard-builtin-why">Still blocked: {b.hard_reason}</div>
              )}
            </div>
          )
        })}
      </div>
      <Typography.Paragraph type="secondary" className="co-guard-help" style={{ marginTop: 12 }}>
        <LockOutlined /> {LOCKED_HELP}
      </Typography.Paragraph>
      <div className="co-guard-builtins">
        {locked.map((b) => (
          <div key={b.id} className="co-guard-builtin co-guard-builtin-locked" data-testid={`builtin-${b.id}`}>
            <Tooltip title="Always on: it keeps co-guard's own reach">
              <Checkbox checked disabled aria-label={`${b.id} (locked)`}>
                <span className="co-guard-builtin-name">{ruleName(b.id)}</span>{' '}
                <span className="co-guard-mono co-guard-builtin-id">{b.id}</span>{' '}
                <Tag bordered={false} icon={<LockOutlined />}>
                  locked
                </Tag>
              </Checkbox>
            </Tooltip>
            <div className="co-guard-builtin-why">{b.reason || b.hard_reason}</div>
          </div>
        ))}
      </div>
    </div>
  )
}

export const GROUND_SWITCHES = [
  ['grounding', 'Grounding', 'Say what was checked and what was not; do not state guesses as facts.'],
  ['short_answers', 'Short answers', 'Keep replies short and to the point.'],
  ['usage_care', 'Usage care', 'Avoid wasteful work; helpers get the same habit.'],
  ['notes', 'Notes', 'Ask the agent to keep short notes of where it stands.'],
  ['questions', 'Questions', 'Ask the agent to file a blocking question, with a recommendation, and end its turn.'],
]
export const GROUND_EXTRA_MAX = 2000
const GROUND_DEFAULTS = {
  grounding: true, short_answers: true, usage_care: true, notes: true, questions: true, extra: '',
}

// Ground rules: the standing instructions every run gets. Always saved whole
// (all six keys); the server refuses a bad value and says why.
export function GroundRulesCard({ canEdit, initial, onSaved }) {
  const [saved, setSaved] = useState({ ...GROUND_DEFAULTS, ...(initial || {}) })
  const [cfg, setCfg] = useState(saved)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)
  useEffect(() => {
    const v = { ...GROUND_DEFAULTS, ...(initial || {}) }
    setSaved(v)
    setCfg(v)
  }, [initial])
  const dirty = GROUND_SWITCHES.some(([k]) => cfg[k] !== saved[k]) || cfg.extra !== saved.extra
  const save = async () => {
    setSaving(true)
    setError(null)
    try {
      const body = {
        grounding: !!cfg.grounding,
        short_answers: !!cfg.short_answers,
        usage_care: !!cfg.usage_care,
        notes: !!cfg.notes,
        questions: !!cfg.questions,
        extra: cfg.extra || '',
      }
      const out = await api.saveGuardOrgSettings({ ground_rules: body })
      const v = { ...body, ...(out?.ground_rules || {}) }
      setSaved(v)
      setCfg(v)
      onSaved?.(v)
      message.success('Ground rules saved')
    } catch (e) {
      setError(e.message)
    } finally {
      setSaving(false)
    }
  }
  return (
    <Card
      className="co-guard-panel"
      title="Ground rules"
      size="small"
      data-testid="ground-rules"
      extra={
        canEdit && (
          <Button type="primary" size="small" loading={saving} disabled={!dirty} onClick={save} data-testid="ground-rules-save">
            Save
          </Button>
        )
      }
    >
      <Typography.Paragraph type="secondary" className="co-guard-help">
        Standing instructions added to every run's system prompt. Only admins can change them.
      </Typography.Paragraph>
      <Space direction="vertical" style={{ width: '100%' }}>
        {GROUND_SWITCHES.map(([k, label, help]) => (
          <div key={k}>
            <Switch
              checked={!!cfg[k]}
              disabled={!canEdit}
              onChange={(v) => setCfg((c) => ({ ...c, [k]: v }))}
              aria-label={label}
              data-testid={`ground-${k}`}
            />{' '}
            <strong>{label}</strong>
            <div className="co-guard-builtin-why">{help}</div>
          </div>
        ))}
        <Input.TextArea
          value={cfg.extra}
          disabled={!canEdit}
          maxLength={GROUND_EXTRA_MAX}
          showCount
          autoSize={{ minRows: 2, maxRows: 8 }}
          placeholder="Anything else every run should know"
          aria-label="Extra ground rules"
          data-testid="ground-extra"
          onChange={(e) => setCfg((c) => ({ ...c, extra: e.target.value }))}
        />
      </Space>
      {error && <Alert type="error" showIcon message={error} data-testid="ground-rules-error" style={{ marginTop: 12 }} />}
    </Card>
  )
}

// Settings → Agent guard (admins edit; the server refuses everyone else).
export default function OrgGuardView({ isAdmin = false }) {
  const [builtins, setBuiltins] = useState([])
  const [saved, setSaved] = useState({ disabled_rules: [], dev_db_patterns: [] })
  const [disabled, setDisabled] = useState([])
  const [patterns, setPatterns] = useState([])
  const [error, setError] = useState(null)
  const [saving, setSaving] = useState(false)
  const [health, setHealth] = useState(null)
  const [ground, setGround] = useState(null)

  const load = useCallback(async () => {
    try {
      const [b, s] = await Promise.all([api.guardBuiltins(), api.guardOrgSettings()])
      setBuiltins(Array.isArray(b) ? b : [])
      const cur = { disabled_rules: s?.disabled_rules || [], dev_db_patterns: s?.dev_db_patterns || [] }
      setSaved(cur)
      setDisabled(cur.disabled_rules)
      setPatterns(cur.dev_db_patterns)
      setGround(s?.ground_rules || {})
    } catch (e) {
      setError(e.message)
    }
  }, [])
  useEffect(() => {
    load()
  }, [load])
  // co-guard's doctor line (hook errors lately); a failed check shows nothing
  useEffect(() => {
    let alive = true
    Promise.resolve()
      .then(() => api.guardHealth?.())
      .then((h) => alive && setHealth(hookErrorsLine(h)))
      .catch(() => alive && setHealth(null))
    return () => {
      alive = false
    }
  }, [])

  const dirty = useMemo(
    () => !sameSet(disabled, saved.disabled_rules) || !sameSet(patterns, saved.dev_db_patterns),
    [disabled, patterns, saved],
  )

  const save = async () => {
    setSaving(true)
    setError(null)
    try {
      const out = await api.saveGuardOrgSettings({ disabled_rules: disabled, dev_db_patterns: patterns })
      const cur = { disabled_rules: out?.disabled_rules || disabled, dev_db_patterns: out?.dev_db_patterns || patterns }
      setSaved(cur)
      setDisabled(cur.disabled_rules)
      setPatterns(cur.dev_db_patterns)
      message.success('Guard settings saved')
    } catch (e) {
      setError(e.message) // e.g. a dev pattern the server refused
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="co-guard-page" data-testid="org-guard">
      <Typography.Title level={4} style={{ marginTop: 0 }}>
        <SafetyOutlined /> Agent guard
      </Typography.Title>
      <Typography.Paragraph type="secondary" className="co-guard-sub">
        The org's action policy for every task: your own rules, which built-in rules apply, and which databases are
        dev. Projects add their own rules, trust and read-only folders in their Guard tab; MCP servers have an
        environment and a default action in MCP Servers.
      </Typography.Paragraph>
      {!isAdmin && (
        <Alert type="info" showIcon style={{ marginBottom: 16 }} message="Only admins can change the org's guard settings." />
      )}
      {health && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 16 }}
          data-testid="guard-health"
          message={health}
          description="A hook that fails is recorded in its run's log; look there for what happened."
        />
      )}

      <Card className="co-guard-panel" title="Org rules" size="small">
        <RuleTable scope="org" canEdit={isAdmin} />
      </Card>

      <Card
        className="co-guard-panel"
        title="Built-in rules"
        size="small"
        extra={
          isAdmin && (
            <Button type="primary" size="small" loading={saving} disabled={!dirty} onClick={save} data-testid="org-guard-save">
              Save
            </Button>
          )
        }
      >
        <Typography.Paragraph type="secondary" className="co-guard-help">
          Unticked rules are off for every project. A project can also turn all built-in rules off in its Guard tab.
        </Typography.Paragraph>
        <BuiltinList builtins={builtins} disabled={disabled} canEdit={isAdmin} onChange={setDisabled} />
      </Card>

      <Card
        className="co-guard-panel"
        title="Dev databases"
        size="small"
        extra={
          isAdmin && (
            <Button type="primary" size="small" loading={saving} disabled={!dirty} onClick={save}>
              Save
            </Button>
          )
        }
      >
        <Typography.Paragraph type="secondary" className="co-guard-help" data-testid="dev-db-help">
          {DEV_DB_HELP}
        </Typography.Paragraph>
        <Space direction="vertical" style={{ width: '100%' }}>
          <Select
            mode="tags"
            style={{ width: '100%' }}
            value={patterns}
            disabled={!isAdmin}
            onChange={(v) => setPatterns(v.map((x) => String(x).trim()).filter(Boolean))}
            tokenSeparators={[',', ' ']}
            open={false}
            suffixIcon={null}
            placeholder="host:*.dev.internal, mcp:dev-postgres, staging-db"
            aria-label="Dev database patterns"
            data-testid="dev-db-patterns"
          />
          <Typography.Text type="secondary" className="co-guard-help">
            Examples: <code>host:*.dev.internal</code> <code>host:db-dev-*</code> <code>mcp:dev-postgres</code>{' '}
            <code>mcp:dev-*</code> <code>staging-db</code>. Not allowed: <code>*</code>, <code>host:*</code>, a bare
            name with <code>*</code>.
          </Typography.Text>
        </Space>
      </Card>
      {ground && <GroundRulesCard canEdit={isAdmin} initial={ground} />}
      {error && <Alert type="error" showIcon message={error} data-testid="org-guard-error" style={{ marginTop: 12 }} />}
    </div>
  )
}
