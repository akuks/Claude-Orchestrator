import { useState } from 'react'
import { Button, Collapse, Form, Input, InputNumber, Segmented, Select, Switch } from 'antd'
import { CheckCircleOutlined, DeleteOutlined, PlusOutlined, RobotOutlined, UserOutlined } from '@ant-design/icons'
import { KIND_HELP, KIND_OPTIONS, checkCommand } from './gates'
import './workflows.css'

const cmdRule = {
  validator: (_, v) => {
    const r = checkCommand(v)
    return r ? Promise.reject(new Error(r)) : Promise.resolve()
  },
}

const KIND_ICON = { agent: <RobotOutlined />, gate: <CheckCircleOutlined />, approval: <UserOutlined /> }

export const BEFORE_EXAMPLES = ['free memory 4 GB', 'port 5432 open', 'container db running']
const DEFAULT_TIMEOUT = 60
const MAX_BEFORE = 5

export function KindSelect({ name, kind }) {
  const form = Form.useFormInstance()
  const onChange = (next) => {
    // Seed what a freshly chosen type needs.
    const cur = form.getFieldValue(['stages', name]) || {}
    const out = { ...cur, kind: next }
    if (next === 'gate') {
      if (!cur.run?.length) out.run = ['']
      if (cur.timeout_min == null) out.timeout_min = DEFAULT_TIMEOUT
    }
    if (next === 'agent' && !cur.items?.length) out.items = [{}]
    form.setFieldValue(['stages', name], out)
  }
  return (
    <Form.Item name={[name, 'kind']} label="What happens in this step" extra={KIND_HELP[kind]} style={{ marginBottom: 14 }}>
      <Segmented
        className="co-wf-kind"
        block
        onChange={onChange}
        options={KIND_OPTIONS.map((o) => ({
          value: o.value,
          label: (
            <span className="co-wf-kind-opt">
              {KIND_ICON[o.value]} {o.label}
            </span>
          ),
        }))}
      />
    </Form.Item>
  )
}

const blank = (v) => !String(v ?? '').trim()

function BeforeList({ name }) {
  const form = Form.useFormInstance()
  return (
    <Form.Item
      label="Wait until ready (optional)"
      extra={`The step starts only when all of these are true. At most ${MAX_BEFORE}.`}
      style={{ marginBottom: 14 }}
    >
      <Form.List name={[name, 'before']}>
        {(rows, { add, remove }) => {
          const cur = form.getFieldValue(['stages', name, 'before']) || []
          const emptyAt = cur.findIndex(blank)
          const full = rows.length >= MAX_BEFORE && emptyAt < 0
          const insert = (text) => {
            if (emptyAt >= 0) form.setFieldValue(['stages', name, 'before', emptyAt], text)
            else if (rows.length < MAX_BEFORE) add(text)
          }
          return (
            <>
              {rows.map((r) => (
                <div key={r.key} className="co-wf-row">
                  <Form.Item name={r.name} noStyle>
                    <Input placeholder="e.g. free memory 4 GB" aria-label="Wait until ready" />
                  </Form.Item>
                  <Button type="text" danger icon={<DeleteOutlined />} onClick={() => remove(r.name)} aria-label="Remove this line" />
                </div>
              ))}
              <div className="co-wf-chips" data-testid="before-examples">
                <span className="co-wf-chips-label">Add an example:</span>
                {BEFORE_EXAMPLES.map((ex) => (
                  <Button key={ex} size="small" shape="round" disabled={full} onClick={() => insert(ex)}>
                    {ex}
                  </Button>
                ))}
                {rows.length < MAX_BEFORE && (
                  <Button size="small" type="dashed" icon={<PlusOutlined />} onClick={() => add('')}>
                    Write your own
                  </Button>
                )}
              </div>
            </>
          )
        }}
      </Form.List>
    </Form.Item>
  )
}

function ProjectSelect({ name, projects, required, label, onChange }) {
  const options = projects.filter((p) => (p.directory || '').trim()).map((p) => ({ value: p.id, label: p.name }))
  return (
    <Form.Item
      name={[name, 'project']}
      label={label}
      rules={required ? [{ required: true, message: 'Pick a project' }] : []}
      style={{ marginBottom: 14 }}
    >
      <Select showSearch optionFilterProp="label" allowClear={!required} placeholder="Pick a project" options={options} onChange={onChange} />
    </Form.Item>
  )
}

const filled = (v) => (Array.isArray(v) ? v.some((x) => !blank(x)) : !blank(v))

// Whether a step's "More options" should open by itself: one of its fields
// has a value (a timeout other than the default counts).
export function moreOptionsAuto(stage, kind) {
  const st = stage || {}
  if (kind === 'gate') {
    return (
      filled(st.retry) ||
      filled(st.before) ||
      filled(st.fix?.agent) ||
      (st.timeout_min != null && st.timeout_min !== DEFAULT_TIMEOUT)
    )
  }
  return filled(st.before) || filled(st.project)
}

const errorUnder = (fieldsError, prefix) =>
  fieldsError.some((f) => f.errors.length > 0 && prefix.every((p, i) => f.name[i] === p))

// Rarely used fields, collapsed. Opens by itself when any of them has a value
// or an error; otherwise the person decides.
function MoreOptions({ name, kind, children }) {
  const [userOpen, setUserOpen] = useState(null)
  return (
    <Form.Item noStyle shouldUpdate={() => true}>
      {({ getFieldValue, getFieldsError }) => {
        const keys = kind === 'gate' ? ['retry', 'before', 'timeout_min', 'fix'] : ['project', 'before']
        const errs = getFieldsError()
        const hasError = keys.some((k) => errorUnder(errs, ['stages', name, k]))
        const open = hasError || (userOpen ?? moreOptionsAuto(getFieldValue(['stages', name]), kind))
        return (
          <div data-testid="more-options" data-open={open ? 'true' : 'false'}>
            <Collapse
              ghost
              size="small"
              className="co-wf-more"
              activeKey={open ? ['more'] : []}
              onChange={(k) => setUserOpen((Array.isArray(k) ? k : [k]).includes('more'))}
              items={[{ key: 'more', label: 'More options', forceRender: true, children }]}
            />
          </div>
        )
      }}
    </Form.Item>
  )
}

// "If the check fails, ask an agent to fix it": only agents of the check's
// project can be picked. The agent is saved by its id.
function FixFields({ name, agents, repick }) {
  return (
    <Form.Item noStyle shouldUpdate>
      {({ getFieldValue }) => {
        const project = getFieldValue(['stages', name, 'project'])
        const chosen = getFieldValue(['stages', name, 'fix', 'agent'])
        const options = agents.filter((a) => project && a.project_id === project).map((a) => ({ value: a.id, label: a.name }))
        if (chosen && !options.some((o) => o.value === chosen)) {
          const known = agents.find((a) => a.id === chosen)
          options.push({ value: chosen, label: known ? `${known.name} (not in this project)` : 'An agent that is no longer here' })
        }
        return (
          <div className="co-wf-fix" data-testid="gate-fix">
            <Form.Item
              name={[name, 'fix', 'agent']}
              label="If the check fails, ask an agent to fix it (optional)"
              extra="The agent gets the failing output, fixes it, and the check runs again."
              style={{ marginBottom: 10 }}
            >
              <Select
                allowClear
                showSearch
                optionFilterProp="label"
                placeholder={project ? 'Pick an agent' : 'Pick where to run it first'}
                notFoundContent={project ? 'No agents in this project' : 'Pick where to run it first'}
                options={options}
                aria-label="Agent that fixes a failed check"
                data-testid="gate-fix-agent"
              />
            </Form.Item>
            {repick && !chosen && (
              <div className="co-wf-muted" data-testid="gate-fix-repick" style={{ marginTop: -6, marginBottom: 10 }}>
                Pick the fixer again for this project
              </div>
            )}
            {chosen && (
              <Form.Item name={[name, 'fix', 'rounds']} label="How many tries" style={{ marginBottom: 14 }}>
                <InputNumber min={1} max={3} precision={0} placeholder="1" style={{ width: '100%' }} data-testid="gate-fix-rounds" />
              </Form.Item>
            )}
          </div>
        )
      }}
    </Form.Item>
  )
}

// focus 'fix' shows only the fixer fields (the canvas's Fixer box); the rest
// stay mounted, hidden, so the form keeps validating them.
function GateFields({ name, projects, agents, focus }) {
  const form = Form.useFormInstance()
  const [repick, setRepick] = useState(false)
  const fixOnly = focus === 'fix'
  // The fixer must belong to the check's project: a new project clears it.
  const projectChanged = () => {
    if (form.getFieldValue(['stages', name, 'fix', 'agent'])) {
      form.setFieldValue(['stages', name, 'fix'], undefined)
      setRepick(true)
    }
  }
  const fixFields = <FixFields name={name} agents={agents} repick={repick} />
  return (
    <>
      <div hidden={fixOnly}>
        <ProjectSelect name={name} projects={projects} required label="Where to run it" onChange={projectChanged} />
        <Form.Item
          label="Commands to run"
          extra="Add one command per box. They run in order and stop at the first one that fails."
          style={{ marginBottom: 14 }}
        >
          <Form.List name={[name, 'run']}>
            {(rows, { add, remove }) => (
              <>
                {rows.map((r) => (
                  <div key={r.key} className="co-wf-row">
                    <Form.Item name={r.name} rules={[cmdRule]} style={{ marginBottom: 6 }}>
                      <Input placeholder="e.g. npm test" aria-label="Command" className="co-wf-mono" />
                    </Form.Item>
                    <Button type="text" danger icon={<DeleteOutlined />} disabled={rows.length <= 1} onClick={() => remove(r.name)} aria-label="Remove command" />
                  </div>
                ))}
                <Button size="small" type="dashed" icon={<PlusOutlined />} disabled={rows.length >= 10} onClick={() => add('')}>
                  Add a command
                </Button>
              </>
            )}
          </Form.List>
        </Form.Item>
      </div>
      {fixOnly && fixFields}
      <div hidden={fixOnly}>
        <MoreOptions name={name} kind="gate">
          <Form.Item noStyle shouldUpdate>
            {({ getFieldValue }) => {
              const run = getFieldValue(['stages', name, 'run']) || []
              const single = run.length === 1
              return (
                <Form.Item
                  name={[name, 'retry']}
                  label="If it fails, re-run only the failures with (optional)"
                  extra={
                    single
                      ? 'Then the full command runs once more. Only that full run can pass the step.'
                      : 'This works only when there is exactly one command.'
                  }
                  dependencies={[['stages', name, 'run']]}
                  rules={[
                    {
                      validator: (_, v) => {
                        if (!v || !single) return Promise.resolve()
                        const r = checkCommand(v)
                        if (r) return Promise.reject(new Error(r))
                        if (v.trim() === (run[0] || '').trim()) return Promise.reject(new Error('this must differ from the command'))
                        return Promise.resolve()
                      },
                    },
                  ]}
                  style={{ marginBottom: 14 }}
                >
                  <Input disabled={!single} placeholder="e.g. npm test -- --only-failures" data-testid="gate-retry" className="co-wf-mono" />
                </Form.Item>
              )
            }}
          </Form.Item>
          {!fixOnly && fixFields}
          <BeforeList name={name} />
          <Form.Item name={[name, 'timeout_min']} label="Give up after (minutes)" style={{ marginBottom: 0 }}>
            <InputNumber min={1} max={720} precision={0} style={{ width: '100%' }} />
          </Form.Item>
        </MoreOptions>
      </div>
    </>
  )
}

function ApprovalFields({ name, projects }) {
  return (
    <>
      <Form.Item name={[name, 'message']} label="What should they check?" rules={[{ max: 2000 }]} style={{ marginBottom: 14 }}>
        <Input.TextArea autoSize={{ minRows: 2, maxRows: 6 }} maxLength={2000} placeholder="e.g. Read the summary and check that the new page looks right." />
      </Form.Item>
      <MoreOptions name={name} kind="approval">
        <ProjectSelect name={name} projects={projects} required={false} label="Project (optional)" />
        <BeforeList name={name} />
      </MoreOptions>
    </>
  )
}

// Fields of one step that depend on its type. `agentFields` is the agents
// editor, rendered only for the "Agents do the work" type.
// "Try once more if it fails" for an agents step.
function AutoRetry({ name }) {
  return (
    <Form.Item
      name={[name, 'auto_retry']}
      valuePropName="checked"
      label="Try once more if it fails"
      extra="Runs the step again one time. Never after someone cancels it."
      style={{ marginTop: 14, marginBottom: 0 }}
    >
      <Switch data-testid="agent-auto-retry" />
    </Form.Item>
  )
}

// showKind: the step type picker (off in the canvas editor, where the type is
// chosen when the step is added). focus: 'fix' for a check's fixer fields only.
export default function StageFields({ name, projects = [], agents = [], agentFields, showKind = true, focus }) {
  return (
    <Form.Item noStyle shouldUpdate>
      {({ getFieldValue }) => {
        const kind = getFieldValue(['stages', name, 'kind']) || 'agent'
        return (
          <>
            {showKind && <KindSelect name={name} kind={kind} />}
            {kind === 'gate' ? (
              <GateFields name={name} projects={projects} agents={agents} focus={focus} />
            ) : kind === 'approval' ? (
              <ApprovalFields name={name} projects={projects} />
            ) : (
              <>
                {agentFields}
                <AutoRetry name={name} />
              </>
            )}
          </>
        )
      }}
    </Form.Item>
  )
}
