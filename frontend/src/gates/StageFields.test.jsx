import { beforeAll, describe, expect, it } from 'vitest'
import { Form } from 'antd'
import { flush, render } from '../home/testRender'
import StageFields from './StageFields'

beforeAll(() => {
  window.matchMedia ||= () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} })
})

function Harness({ stage }) {
  return (
    <Form initialValues={{ stages: [stage] }}>
      <Form.List name="stages">
        {(fields) => fields.map((f) => <StageFields key={f.key} name={f.name} projects={[{ id: 'p1', name: 'Proj', directory: '/x' }]} agentFields={<div data-testid="agent-items" />} />)}
      </Form.List>
    </Form>
  )
}

describe('StageFields', () => {
  it('gate shows gate fields and hides agent items', async () => {
    const v = await render(<Harness stage={{ name: 'g', kind: 'gate', run: ['pytest -q'], timeout_min: 60 }} />)
    await flush()
    expect(v.container.querySelector('[aria-label="Command"]')).toBeTruthy()
    expect(v.container.querySelector('[data-testid="agent-items"]')).toBeNull()
    expect(v.container.querySelector('[aria-label="Retry command"]').disabled).toBe(false)
    await v.unmount()
  })
  it('retry is disabled with two commands', async () => {
    const v = await render(<Harness stage={{ name: 'g', kind: 'gate', run: ['a', 'b'] }} />)
    await flush()
    expect(v.container.querySelector('[aria-label="Retry command"]').disabled).toBe(true)
    await v.unmount()
  })
  it('agent stage shows its items', async () => {
    const v = await render(<Harness stage={{ name: 'a', kind: 'agent' }} />)
    await flush()
    expect(v.container.querySelector('[data-testid="agent-items"]')).toBeTruthy()
    await v.unmount()
  })
  it('approval shows message', async () => {
    const v = await render(<Harness stage={{ name: 'a', kind: 'approval', message: 'ok?' }} />)
    await flush()
    expect(v.container.querySelector('textarea').value).toBe('ok?')
    expect(v.container.querySelector('[aria-label="Command"]')).toBeNull()
    await v.unmount()
  })
})
