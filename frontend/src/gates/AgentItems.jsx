import { useState } from 'react'
import { Button, Collapse, Form, Input, Select } from 'antd'
import { DeleteOutlined, PlusOutlined } from '@ant-design/icons'
import { MAX_OUTPUTS, OUTPUT_NAME, appendPlaceholder, earlierOutputs } from './gates'
import './workflows.css'

export const INSERTS = [
  { text: '{{input}}', label: 'What you run it on', fromStep: 0 },
  { text: '{{prev}}', label: 'Result of the previous step', fromStep: 1 },
]

const outputsRule = {
  validator: (_, v) => {
    const list = Array.isArray(v) ? v : []
    const bad = list.find((n) => !OUTPUT_NAME.test(String(n)))
    if (bad != null) return Promise.reject(new Error(`"${bad}" can only use lowercase letters, numbers and _, and must start with a letter`))
    if (list.length > MAX_OUTPUTS) return Promise.reject(new Error(`At most ${MAX_OUTPUTS} names`))
    return Promise.resolve()
  },
}

// Rarely used settings of one agent, collapsed. Opens by itself when the agent
// already hands something over.
function AgentMore({ stageName, itemName }) {
  const [userOpen, setUserOpen] = useState(null)
  const form = Form.useFormInstance()
  const outputs = Form.useWatch(['stages', stageName, 'items', itemName, 'outputs'], form)
  const open = userOpen ?? (Array.isArray(outputs) && outputs.length > 0)
  return (
    <div data-testid="agent-more" data-open={open ? 'true' : 'false'}>
      <Collapse
        ghost
        size="small"
        className="co-wf-more"
        activeKey={open ? ['more'] : []}
        onChange={(k) => setUserOpen((Array.isArray(k) ? k : [k]).includes('more'))}
        items={[
          {
            key: 'more',
            label: 'More options',
            forceRender: true,
            children: (
              <Form.Item
                name={[itemName, 'outputs']}
                label="What should this agent hand over? (optional)"
                extra="Names for things later steps can use, like plan_path or summary. Use lowercase letters, numbers and _."
                rules={[outputsRule]}
                style={{ marginBottom: 0 }}
              >
                <Select
                  mode="tags"
                  open={false}
                  suffixIcon={null}
                  tokenSeparators={[',', ' ']}
                  placeholder="e.g. plan_path"
                  style={{ width: '100%' }}
                  aria-label="Names this agent hands over"
                  data-testid="agent-outputs"
                />
              </Form.Item>
            ),
          },
        ]}
      />
    </div>
  )
}

// The agents of one "Agents do the work" step. Each agent stacks: who does it,
// then what they should do, then buttons that insert the shared values.
// `stageName` is the Form.List name of the step, `stepIndex` its position.
export default function AgentItems({ stageName, stepIndex, agentOptions = [] }) {
  const form = Form.useFormInstance()
  const stages = Form.useWatch('stages', form)
  const named = earlierOutputs(stages, stepIndex)
  const twice = new Set(named.filter((o, i) => named.findIndex((x) => x.name === o.name) !== i).map((o) => o.name))
  const insert = (itemName, text) => {
    const path = ['stages', stageName, 'items', itemName, 'prompt']
    form.setFieldValue(path, appendPlaceholder(form.getFieldValue(path), text))
  }
  return (
    <Form.List name={[stageName, 'items']}>
      {(items, { add, remove }) => (
        <div className="co-wf-agents">
          {items.map((item, ai) => (
            <div key={item.key} className="co-wf-agent" data-testid="agent-item">
              <div className="co-wf-agent-top">
                <Form.Item
                  name={[item.name, 'agent']}
                  label={items.length > 1 ? `Agent ${ai + 1}` : 'Agent'}
                  rules={[{ required: true, message: 'Pick an agent' }]}
                  style={{ marginBottom: 8, flex: 1, minWidth: 0 }}
                >
                  <Select showSearch placeholder="Pick an agent" options={agentOptions} />
                </Form.Item>
                <Button
                  type="text"
                  danger
                  icon={<DeleteOutlined />}
                  onClick={() => remove(item.name)}
                  aria-label={`Remove agent ${ai + 1}`}
                  disabled={items.length <= 1}
                />
              </div>
              <Form.Item
                name={[item.name, 'prompt']}
                label="What should this agent do?"
                rules={[{ required: true, message: 'Tell the agent what to do' }]}
                style={{ marginBottom: 6 }}
              >
                <Input.TextArea autoSize={{ minRows: 3, maxRows: 12 }} placeholder="e.g. Build the feature described here: " />
              </Form.Item>
              <div className="co-wf-inserts">
                <span className="co-wf-chips-label">Insert:</span>
                {INSERTS.filter((i) => stepIndex >= i.fromStep).map((i) => (
                  <Button
                    key={i.text}
                    size="small"
                    shape="round"
                    icon={<PlusOutlined />}
                    onClick={() => insert(item.name, i.text)}
                    data-testid={`insert-${i.text === '{{input}}' ? 'input' : 'prev'}`}
                  >
                    {i.label}
                  </Button>
                ))}
                {named.map((o) => (
                  <Button
                    key={o.text}
                    size="small"
                    shape="round"
                    icon={<PlusOutlined />}
                    title={`What step ${o.step} handed over as ${o.name}`}
                    onClick={() => insert(item.name, o.text)}
                    data-testid="insert-output"
                  >
                    {twice.has(o.name) ? `${o.name} (step ${o.step})` : o.name}
                  </Button>
                ))}
              </div>
              <AgentMore stageName={stageName} itemName={item.name} />
            </div>
          ))}
          <Button size="small" type="dashed" icon={<PlusOutlined />} onClick={() => add({})}>
            Add another agent
          </Button>
        </div>
      )}
    </Form.List>
  )
}
