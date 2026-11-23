import { useEffect, useMemo, useState } from 'react'
import { Alert, Button, Divider, Input, InputNumber, Select, Slider, Space, Switch, Tag, Typography, message } from 'antd'
import { DeleteOutlined, EyeInvisibleOutlined, FileTextOutlined, PlusOutlined, SafetyOutlined } from '@ant-design/icons'
import { api } from '../api'
import RuleTable from './RuleTable'
import {
  GUARD_SETTINGS_HELP,
  NO_ANSWER_HELP,
  QUARANTINE_HELP,
  READONLY_HELP,
  REFERENCES_HELP,
  TRUST_HELP,
  WAIT_MAX,
  revealHidden,
  WAIT_MIN,
  formatWait,
} from './guard'
import './guard.css'

const listOf = (v) => (Array.isArray(v) ? v : [])
const cleanPaths = (rows) =>
  rows.map((r) => ({ path: String(r.path || '').trim(), reason: String(r.reason || '').trim() })).filter((r) => r.path)
const samePaths = (a, b) =>
  a.length === b.length && a.every((r, i) => r.path === b[i].path && (r.reason || '') === (b[i].reason || ''))
const sameIds = (a, b) => a.length === b.length && a.every((x) => b.includes(x))

function initial(project) {
  return {
    enabled: project.guard_enabled !== false,
    wait: project.approval_wait_seconds || 300,
    trust: project.trust === 'external' ? 'external' : 'own',
    quarantine: Boolean(project.quarantine),
    onNoAnswer: project.on_no_answer === 'deny_and_resume' ? 'deny_and_resume' : 'deny',
    readonly: listOf(project.readonly_paths).map((r) => ({ path: r?.path || '', reason: r?.reason || '' })),
    refs: listOf(project.reference_project_ids).filter((x) => typeof x === 'string'),
  }
}

// Repo text (the least-trusted on this page): hidden characters are shown as
// ⟦U+XXXX⟧ markers, with a chip after the value.
function RepoText({ value, testid }) {
  const { text, count } = revealHidden(value)
  return (
    <>
      {text}
      {count > 0 && (
        <Tag className="co-guard-hidden-chip" icon={<EyeInvisibleOutlined />} style={{ marginLeft: 6 }} data-testid={testid}>
          hidden characters
        </Tag>
      )}
    </>
  )
}

// The repo's .claude-orchestrator/rules.md as the worker reads it.
function RulesFileStatus({ projectId }) {
  const [info, setInfo] = useState(null)
  const [error, setError] = useState(null)
  useEffect(() => {
    let live = true
    const p = api.projectRulesFile?.(projectId)
    p?.then((r) => live && setInfo(r)).catch((e) => live && setError(e.message))
    return () => {
      live = false
    }
  }, [projectId])
  if (error) return <Alert type="error" showIcon message={error} />
  if (!info) return null
  const rules = [...listOf(info.confirm), ...listOf(info.never)]
  const errors = listOf(info.errors)
  return (
    <div data-testid="rules-file">
      <Typography.Paragraph type="secondary" className="co-guard-help">
        <FileTextOutlined /> <span className="co-guard-mono">{info.path}</span> — the repo's own rules (## Confirm and
        ## Never lines), read when a task starts. They can only add rules; anything else in the file is ignored.
      </Typography.Paragraph>
      {!info.read ? (
        <Alert
          type="info"
          showIcon
          data-testid="rules-file-not-read"
          message={`Not read: this project is external${info.present ? ' (the file exists)' : ''}`}
          description="An external repo's rules file is never read: someone else's code cannot change your policy."
        />
      ) : !info.present ? (
        <Typography.Text type="secondary" data-testid="rules-file-absent">
          No rules file in this repo.
        </Typography.Text>
      ) : (
        <>
          <Typography.Text data-testid="rules-file-count">
            Read: {rules.length} rule{rules.length === 1 ? '' : 's'}
          </Typography.Text>
          {rules.length > 0 && (
            <ul className="co-guard-file-rules">
              {rules.map((r) => (
                <li key={r.id}>
                  <Tag bordered={false} color={r.kind === 'never' ? 'red' : 'orange'}>
                    {r.kind}
                  </Tag>
                  <span className="co-guard-mono">
                    <RepoText value={r.pattern} testid="rules-file-hidden" />
                  </span>
                  {r.reason ? (
                    <Typography.Text type="secondary">
                      {' '}
                      — <RepoText value={r.reason} testid="rules-file-hidden" />
                    </Typography.Text>
                  ) : null}
                  <Typography.Text type="secondary"> (line {r.line})</Typography.Text>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
      {errors.length > 0 && (
        <Alert
          type="warning"
          showIcon
          style={{ marginTop: 8 }}
          data-testid="rules-file-errors"
          message="Lines ignored"
          description={
            <ul className="co-guard-file-rules">
              {errors.map((e, i) => (
                <li key={i}>
                  {e.line ? `line ${e.line}: ` : ''}
                  <RepoText value={e.message} testid="rules-file-hidden" />
                </li>
              ))}
            </ul>
          }
        />
      )}
    </div>
  )
}

// Project drawer → Guard tab: co-guard on/off, the approval wait and the
// project's action policy (trust, quarantine, no-answer mode, read-only
// folders, reference projects, project rules). Maintainers and admins edit;
// everyone else reads.
export default function GuardSettings({ project, canEdit, onSaved, ruleDraft, onRuleDraftUsed }) {
  const start = useMemo(() => initial(project), [project])
  const [v, setV] = useState(start)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)
  const [projects, setProjects] = useState([])
  const set = (patch) => setV((cur) => ({ ...cur, ...patch }))

  useEffect(() => {
    setV(initial(project))
    setError(null)
  }, [project])

  useEffect(() => {
    let live = true
    const p = api.listProjects?.()
    p?.then((list) => live && setProjects(Array.isArray(list) ? list : [])).catch(() => {})
    return () => {
      live = false
    }
  }, [])

  const clamp = (x) => Math.min(WAIT_MAX, Math.max(WAIT_MIN, Math.round(Number(x) || WAIT_MIN)))
  const paths = cleanPaths(v.readonly)
  const changes = {}
  if (v.trust !== start.trust) changes.trust = v.trust
  if (v.quarantine !== start.quarantine) changes.quarantine = v.quarantine
  if (v.onNoAnswer !== start.onNoAnswer) changes.on_no_answer = v.onNoAnswer
  if (!samePaths(paths, cleanPaths(start.readonly)))
    changes.readonly_paths = paths.map((r) => (r.reason ? r : { path: r.path }))
  if (!sameIds(v.refs, start.refs)) changes.reference_project_ids = v.refs
  const dirty = v.enabled !== start.enabled || v.wait !== start.wait || Object.keys(changes).length > 0

  const save = async () => {
    setSaving(true)
    setError(null)
    try {
      // Only the policy fields that changed: an unchanged reference list is not re-checked.
      await api.updateProject(project.id, { guard_enabled: v.enabled, approval_wait_seconds: clamp(v.wait), ...changes })
      message.success('Guard settings saved')
      onSaved?.()
    } catch (e) {
      setError(e.message)
    } finally {
      setSaving(false)
    }
  }

  const refOptions = useMemo(() => {
    const visible = projects.filter((p) => p.id !== project.id).map((p) => ({ value: p.id, label: p.name }))
    const known = new Set(visible.map((o) => o.value))
    const hidden = v.refs.filter((id) => !known.has(id)).map((id) => ({ value: id, label: 'a project you cannot see' }))
    return [...visible, ...hidden]
  }, [projects, project.id, v.refs])

  const setRow = (i, patch) => set({ readonly: v.readonly.map((r, j) => (j === i ? { ...r, ...patch } : r)) })

  return (
    <div data-testid="guard-settings">
      <Typography.Paragraph type="secondary" data-testid="guard-help">
        <SafetyOutlined /> {GUARD_SETTINGS_HELP}
      </Typography.Paragraph>

      <div className="co-guard-settings-row">
        <Switch
          checked={v.enabled}
          disabled={!canEdit}
          onChange={(x) => set({ enabled: x })}
          data-testid="guard-enabled"
          aria-label="Built-in guard rules"
        />
        <Typography.Text strong>Built-in rules {v.enabled ? 'on' : 'off'}</Typography.Text>
      </div>
      {!v.enabled && (
        <Alert
          data-testid="guard-off-warning"
          type="warning"
          showIcon
          style={{ marginBottom: 16 }}
          message="Dangerous shell commands will run without a check"
          description="MCP approvals and blocks, self-protection, custom rules, quarantine and read-only folders still apply."
        />
      )}

      <Typography.Text strong>Approval wait</Typography.Text>
      <Typography.Paragraph type="secondary" style={{ marginBottom: 8 }}>
        How long a paused action waits for a person before it is blocked:{' '}
        <span className="co-num" data-testid="guard-wait-label">
          {formatWait(clamp(v.wait))}
        </span>
      </Typography.Paragraph>
      <div className="co-guard-settings-row">
        <Slider
          className="co-guard-settings-slider"
          min={WAIT_MIN}
          max={WAIT_MAX}
          step={30}
          value={clamp(v.wait)}
          disabled={!canEdit}
          onChange={(x) => set({ wait: x })}
          tooltip={{ formatter: formatWait }}
          marks={{ 60: '1m', 300: '5m', 600: '10m', 900: '15m' }}
          aria-label="Approval wait"
        />
        <span data-testid="guard-wait-input">
          <InputNumber
            min={WAIT_MIN}
            max={WAIT_MAX}
            step={30}
            value={v.wait}
            disabled={!canEdit}
            onChange={(x) => x != null && set({ wait: x })}
            onBlur={() => set({ wait: clamp(v.wait) })}
            suffix="s"
            style={{ width: 130 }}
            aria-label="Approval wait in seconds"
          />
        </span>
      </div>

      <Typography.Text strong>When nobody answers</Typography.Text>
      <div className="co-guard-settings-row co-guard-settings-stack">
        <span data-testid="guard-no-answer">
          <Select
            value={v.onNoAnswer}
            disabled={!canEdit}
            onChange={(x) => set({ onNoAnswer: x })}
            style={{ width: 300 }}
            aria-label="When nobody answers"
            options={[
              { value: 'deny', label: 'Block the call' },
              { value: 'deny_and_resume', label: 'Block, stop, resume on approval' },
            ]}
          />
        </span>
        <Typography.Text type="secondary" className="co-guard-help" data-testid="guard-no-answer-help">
          {NO_ANSWER_HELP[v.onNoAnswer]}
        </Typography.Text>
      </div>

      <Divider orientation="left" plain>
        Repo trust
      </Divider>
      <div className="co-guard-settings-row co-guard-settings-stack">
        <span data-testid="guard-trust">
          <Select
            value={v.trust}
            disabled={!canEdit}
            onChange={(x) => set({ trust: x })}
            style={{ width: 300 }}
            aria-label="Repo trust"
            options={[
              { value: 'own', label: 'Own code' },
              { value: 'external', label: "External (someone else's code)" },
            ]}
          />
        </span>
        {project.trust_reason && (
          <Typography.Text className="co-guard-help" data-testid="guard-trust-reason">
            Suggested: {project.trust_reason}
          </Typography.Text>
        )}
        <Typography.Text type="secondary" className="co-guard-help" data-testid="guard-trust-help">
          {TRUST_HELP}
        </Typography.Text>
      </div>
      <div className="co-guard-settings-row">
        <Switch
          checked={v.quarantine}
          disabled={!canEdit}
          onChange={(x) => set({ quarantine: x })}
          data-testid="guard-quarantine"
          aria-label="Quarantine"
        />
        <Typography.Text strong>Quarantine {v.quarantine ? 'on' : 'off'}</Typography.Text>
      </div>
      <Typography.Paragraph type="secondary" className="co-guard-help">
        {QUARANTINE_HELP}
      </Typography.Paragraph>

      <Divider orientation="left" plain>
        Read-only folders
      </Divider>
      <Typography.Paragraph type="secondary" className="co-guard-help" data-testid="guard-readonly-help">
        {READONLY_HELP}
      </Typography.Paragraph>
      <div data-testid="guard-readonly-paths">
        {v.readonly.map((r, i) => (
          <div key={i} className="co-guard-path-row">
            <Input
              value={r.path}
              disabled={!canEdit}
              onChange={(e) => setRow(i, { path: e.target.value })}
              placeholder="legacy/ or /abs/path"
              className="co-guard-mono"
              aria-label={`Read-only path ${i + 1}`}
              maxLength={1000}
            />
            <Input
              value={r.reason}
              disabled={!canEdit}
              onChange={(e) => setRow(i, { reason: e.target.value })}
              placeholder="Reason (optional)"
              aria-label={`Reason for read-only path ${i + 1}`}
              maxLength={200}
            />
            {canEdit && (
              <Button
                type="text"
                danger
                icon={<DeleteOutlined />}
                aria-label={`Remove read-only path ${i + 1}`}
                onClick={() => set({ readonly: v.readonly.filter((_, j) => j !== i) })}
              />
            )}
          </div>
        ))}
        {canEdit && (
          <Button
            size="small"
            icon={<PlusOutlined />}
            onClick={() => set({ readonly: [...v.readonly, { path: '', reason: '' }] })}
            data-testid="guard-readonly-add"
          >
            Add folder
          </Button>
        )}
        {!canEdit && v.readonly.length === 0 && <Typography.Text type="secondary">None</Typography.Text>}
      </div>

      <Divider orientation="left" plain>
        Reference projects
      </Divider>
      <Typography.Paragraph type="secondary" className="co-guard-help">
        {REFERENCES_HELP}
      </Typography.Paragraph>
      <span data-testid="guard-references">
        <Select
          mode="multiple"
          style={{ width: '100%' }}
          value={v.refs}
          disabled={!canEdit}
          onChange={(x) => set({ refs: x })}
          options={refOptions}
          optionFilterProp="label"
          placeholder="None"
          aria-label="Reference projects"
        />
      </span>

      <Space style={{ marginTop: 16, display: 'flex' }} direction="vertical">
        {error && <Alert type="error" showIcon message={error} data-testid="guard-save-error" />}
        {canEdit ? (
          <Button type="primary" loading={saving} disabled={!dirty} onClick={save}>
            Save guard settings
          </Button>
        ) : (
          <Typography.Text type="secondary" data-testid="guard-readonly">
            Only project maintainers and admins can change these settings.
          </Typography.Text>
        )}
      </Space>

      <Divider orientation="left" plain>
        Project rules
      </Divider>
      <RuleTable scope="project" projectId={project.id} canEdit={canEdit} draft={ruleDraft} onDraftUsed={onRuleDraftUsed} />

      <Divider orientation="left" plain>
        Repo rules file
      </Divider>
      <RulesFileStatus key={`${project.id}:${project.trust}`} projectId={project.id} />

      <Divider orientation="left" plain>
        Org rules (read-only here)
      </Divider>
      <RuleTable scope="org" canEdit={false} help={false} />
    </div>
  )
}
