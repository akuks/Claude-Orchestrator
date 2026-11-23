import { Button, Form, Input, Select } from 'antd'
import { DeleteOutlined, PlusOutlined } from '@ant-design/icons'
import { appendPlaceholder } from './gates'
import './workflows.css'

export const INSERTS = [
  { text: '{{input}}', label: 'What you run it on', fromStep: 0 },
  { text: '{{prev}}', label: 'Result of the previous step', fromStep: 1 },
]

// The agents of one "Agents do the work" step. Each agent stacks: who does it,
// then what they should do, then buttons that insert the shared values.
// `stageName` is the Form.List name of the step, `stepIndex` its position.
export default function AgentItems({ stageName, stepIndex, agentOptions = [] }) {
  const form = Form.useFormInstance()
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
              </div>
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
