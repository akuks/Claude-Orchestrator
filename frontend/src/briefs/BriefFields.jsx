import { useState } from 'react'
import { Collapse, Form, Input, InputNumber, Select } from 'antd'

const tags = (placeholder) => (
  <Select mode="tags" tokenSeparators={[',']} placeholder={placeholder} open={false} suffixIcon={null} />
)

// The collapsible Brief section of a task form. Fields are `brief.*`; the
// parent form owns the values (briefPayload turns them into the request).
export default function BriefFields({ error }) {
  const [keys, setKeys] = useState([])
  return (
    <Collapse
      activeKey={error ? ['brief'] : keys}
      onChange={(k) => setKeys([].concat(k))}
      size="small"
      style={{ marginBottom: 16 }}
      items={[
        {
          key: 'brief',
          label: 'Brief',
          forceRender: true,
          children: (
            <div data-testid="brief-fields">
              <Form.Item name={['brief', 'goal']} label="Goal">
                <Input.TextArea rows={2} placeholder="What the task is for" />
              </Form.Item>
              <Form.Item name={['brief', 'can_change']} label="CAN change" extra="Paths or globs the task may change">
                {tags('e.g. src/**')}
              </Form.Item>
              <Form.Item name={['brief', 'cannot_change']} label="CANNOT change" extra="Paths or globs the task must not change">
                {tags('e.g. migrations/**')}
              </Form.Item>
              <Form.Item name={['brief', 'done']} label="Done">
                <Input.TextArea rows={2} placeholder="When is the task done?" />
              </Form.Item>
              <Form.Item name={['brief', 'stop']} label="Stop">
                <Input.TextArea rows={2} placeholder="When must it stop and ask?" />
              </Form.Item>
              <Collapse
                size="small"
                ghost
                items={[
                  {
                    key: 'more',
                    label: 'More',
                    forceRender: true,
                    children: (
                      <>
                        <Form.Item name={['brief', 'setup']} label="Setup">
                          <Input.TextArea rows={2} />
                        </Form.Item>
                        <Form.Item name={['brief', 'log']} label="Log">
                          <Input.TextArea rows={2} />
                        </Form.Item>
                        <Form.Item name={['brief', 'retries']} label="Retries">
                          <InputNumber min={0} max={10} style={{ width: '100%' }} />
                        </Form.Item>
                        <Form.Item name={['brief', 'tie_break']} label="Tie-break">
                          <Input.TextArea rows={2} />
                        </Form.Item>
                        <Form.Item name={['brief', 'outputs']} label="Outputs">
                          {tags('names of results to hand back')}
                        </Form.Item>
                      </>
                    ),
                  },
                ]}
              />
              {error && (
                <div role="alert" data-testid="brief-error" style={{ color: 'var(--ant-color-error, #cf1322)' }}>
                  {error}
                </div>
              )}
            </div>
          ),
        },
      ]}
    />
  )
}
