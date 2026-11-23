import { Button, Form, Input, InputNumber, Select, Space } from 'antd'
import { DeleteOutlined, PlusOutlined } from '@ant-design/icons'
import { KIND_OPTIONS, checkCommand } from './gates'

const cmdRule = {
  validator: (_, v) => {
    const r = checkCommand(v)
    return r ? Promise.reject(new Error(r)) : Promise.resolve()
  },
}

export function KindSelect({ name }) {
  const form = Form.useFormInstance()
  const onChange = (kind) => {
    // Seed what a freshly chosen kind needs.
    const cur = form.getFieldValue(['stages', name]) || {}
    const next = { ...cur, kind }
    if (kind === 'gate') {
      if (!cur.run?.length) next.run = ['']
      if (cur.timeout_min == null) next.timeout_min = 60
    }
    if (kind === 'agent' && !cur.items?.length) next.items = [{}]
    form.setFieldValue(['stages', name], next)
  }
  return (
    <Form.Item name={[name, 'kind']} label="Kind" style={{ marginBottom: 8 }}>
      <Select style={{ width: 200 }} options={KIND_OPTIONS} onChange={onChange} />
    </Form.Item>
  )
}

function BeforeList({ name }) {
  return (
    <Form.Item label="Before (pre-flight checks, at most 5)" style={{ marginBottom: 8 }}>
      <Form.List name={[name, 'before']}>
        {(rows, { add, remove }) => (
          <>
            {rows.map((r) => (
              <Space key={r.key} align="start" style={{ display: 'flex', marginBottom: 4 }}>
                <Form.Item name={r.name} noStyle>
                  <Input style={{ width: 360 }} placeholder="free memory 4 GB" aria-label="Pre-flight line" />
                </Form.Item>
                <Button type="text" danger icon={<DeleteOutlined />} onClick={() => remove(r.name)} aria-label="Remove pre-flight line" />
              </Space>
            ))}
            {rows.length < 5 && (
              <Button size="small" type="dashed" icon={<PlusOutlined />} onClick={() => add('')}>
                Add pre-flight check
              </Button>
            )}
          </>
        )}
      </Form.List>
    </Form.Item>
  )
}

function ProjectSelect({ name, projects, required }) {
  const options = projects.filter((p) => (p.directory || '').trim()).map((p) => ({ value: p.id, label: p.name }))
  return (
    <Form.Item
      name={[name, 'project']}
      label={required ? 'Project' : 'Project (optional)'}
      rules={required ? [{ required: true, message: 'Pick a project' }] : []}
      style={{ marginBottom: 8 }}
    >
      <Select showSearch optionFilterProp="label" allowClear={!required} style={{ width: 300 }} placeholder="Project with a directory" options={options} />
    </Form.Item>
  )
}

function GateFields({ name, projects }) {
  return (
    <>
      <ProjectSelect name={name} projects={projects} required />
      <Form.Item label="Commands (1 to 10, run in order, stop at the first failure)" style={{ marginBottom: 8 }}>
        <Form.List name={[name, 'run']}>
          {(rows, { add, remove }) => (
            <>
              {rows.map((r) => (
                <Space key={r.key} align="start" style={{ display: 'flex', marginBottom: 4 }}>
                  <Form.Item name={r.name} rules={[cmdRule]} style={{ marginBottom: 0 }}>
                    <Input style={{ width: 460 }} placeholder="pytest -q" aria-label="Command" />
                  </Form.Item>
                  <Button type="text" danger icon={<DeleteOutlined />} disabled={rows.length <= 1} onClick={() => remove(r.name)} aria-label="Remove command" />
                </Space>
              ))}
              <Button size="small" type="dashed" icon={<PlusOutlined />} disabled={rows.length >= 10} onClick={() => add('')}>
                Add command
              </Button>
            </>
          )}
        </Form.List>
      </Form.Item>
      <Form.Item noStyle shouldUpdate>
        {({ getFieldValue }) => {
          const run = getFieldValue(['stages', name, 'run']) || []
          const single = run.length === 1
          return (
            <Form.Item
              name={[name, 'retry']}
              label="Retry command"
              extra={single ? 'Run once more if the command fails.' : 'Retry works only with exactly one command.'}
              dependencies={[['stages', name, 'run']]}
              rules={[
                {
                  validator: (_, v) => {
                    if (!v || !single) return Promise.resolve()
                    const r = checkCommand(v)
                    if (r) return Promise.reject(new Error(r))
                    if (v.trim() === (run[0] || '').trim()) return Promise.reject(new Error('retry must differ from the command'))
                    return Promise.resolve()
                  },
                },
              ]}
              style={{ marginBottom: 8 }}
            >
              <Input style={{ width: 460 }} disabled={!single} placeholder="optional" aria-label="Retry command" />
            </Form.Item>
          )
        }}
      </Form.Item>
      <BeforeList name={name} />
      <Form.Item name={[name, 'timeout_min']} label="Timeout (minutes)" style={{ marginBottom: 0 }}>
        <InputNumber min={1} max={720} precision={0} />
      </Form.Item>
    </>
  )
}

function ApprovalFields({ name, projects }) {
  return (
    <>
      <Form.Item name={[name, 'message']} label="Message" rules={[{ max: 2000 }]} style={{ marginBottom: 8 }}>
        <Input.TextArea autoSize={{ minRows: 2, maxRows: 6 }} maxLength={2000} placeholder="What the approver should check" />
      </Form.Item>
      <ProjectSelect name={name} projects={projects} required={false} />
      <BeforeList name={name} />
    </>
  )
}

// Fields of one stage that depend on its kind. `agentFields` is the existing
// agent items editor, rendered only for the Agents kind.
export default function StageFields({ name, projects = [], agentFields }) {
  return (
    <>
      <KindSelect name={name} />
      <Form.Item noStyle shouldUpdate>
        {({ getFieldValue }) => {
          const kind = getFieldValue(['stages', name, 'kind']) || 'agent'
          if (kind === 'gate') return <GateFields name={name} projects={projects} />
          if (kind === 'approval') return <ApprovalFields name={name} projects={projects} />
          return agentFields
        }}
      </Form.Item>
    </>
  )
}
