import { beforeAll, describe, expect, it } from 'vitest'
import { act } from 'react'
import { Form } from 'antd'
import { flush, render } from '../home/testRender'
import StageFields, { moreOptionsAuto } from './StageFields'
import AgentItems from './AgentItems'

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
    expect(v.container.querySelector('[data-testid="gate-retry"]').disabled).toBe(false)
    await v.unmount()
  })
  it('retry is disabled with two commands', async () => {
    const v = await render(<Harness stage={{ name: 'g', kind: 'gate', run: ['a', 'b'] }} />)
    await flush()
    expect(v.container.querySelector('[data-testid="gate-retry"]').disabled).toBe(true)
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

const click = async (el) => {
  await act(async () => el.dispatchEvent(new MouseEvent('click', { bubbles: true })))
  await flush()
}
const moreOpen = (c) => c.querySelector('[data-testid="more-options"]').dataset.open

describe('More options', () => {
  it('stays closed for a plain check step', async () => {
    const v = await render(<Harness stage={{ name: 'g', kind: 'gate', run: ['pytest -q'], timeout_min: 60, before: [] }} />)
    await flush()
    expect(moreOpen(v.container)).toBe('false')
    expect(v.container.querySelector('.ant-collapse-item-active')).toBeNull()
    await v.unmount()
  })
  it('opens by itself when a re-run, a wait line or a non-default time limit is set', async () => {
    for (const extra of [{ retry: 'pytest --lf' }, { before: ['port 5432 open'] }, { timeout_min: 30 }]) {
      const v = await render(<Harness stage={{ name: 'g', kind: 'gate', run: ['pytest -q'], timeout_min: 60, ...extra }} />)
      await flush()
      expect(moreOpen(v.container)).toBe('true')
      expect(v.container.querySelector('.ant-collapse-item-active')).not.toBeNull()
      await v.unmount()
    }
  })
  it('opens by itself when one of its fields has an error', async () => {
    let form
    function H() {
      const [f] = Form.useForm()
      form = f
      return (
        <Form form={f} initialValues={{ stages: [{ name: 'g', kind: 'gate', run: ['pytest -q'], timeout_min: 60 }] }}>
          <Form.List name="stages">
            {(fields) => fields.map((x) => <StageFields key={x.key} name={x.name} projects={[]} />)}
          </Form.List>
        </Form>
      )
    }
    const v = await render(<H />)
    await flush()
    expect(moreOpen(v.container)).toBe('false')
    await act(async () => form.setFields([{ name: ['stages', 0, 'timeout_min'], errors: ['too long'] }]))
    await flush()
    expect(moreOpen(v.container)).toBe('true')
    await v.unmount()
  })
  it('moreOptionsAuto', () => {
    expect(moreOptionsAuto({ timeout_min: 60, before: [''] }, 'gate')).toBe(false)
    expect(moreOptionsAuto({ retry: ' x ' }, 'gate')).toBe(true)
    expect(moreOptionsAuto({ project: 'p1' }, 'approval')).toBe(true)
    expect(moreOptionsAuto({}, 'approval')).toBe(false)
  })
  it('an example chip fills the wait-until-ready list', async () => {
    const v = await render(<Harness stage={{ name: 'g', kind: 'gate', run: ['pytest -q'], timeout_min: 60 }} />)
    await flush()
    const chip = [...v.container.querySelectorAll('[data-testid="before-examples"] button')].find((b) => b.textContent === 'port 5432 open')
    await click(chip)
    expect([...v.container.querySelectorAll('[aria-label="Wait until ready"]')].map((i) => i.value)).toEqual(['port 5432 open'])
    await v.unmount()
  })
})

describe('AgentItems insert buttons', () => {
  function H({ stepIndex, prompt }) {
    return (
      <Form initialValues={{ stages: [{ name: 's', kind: 'agent', items: [{ agent: 'a', prompt }] }] }}>
        <Form.List name="stages">
          {(fields) => fields.map((f) => <AgentItems key={f.key} stageName={f.name} stepIndex={stepIndex} agentOptions={[]} />)}
        </Form.List>
      </Form>
    )
  }
  it('adds the placeholder at the end of the prompt', async () => {
    const v = await render(<H stepIndex={1} prompt="Review this" />)
    await flush()
    await click(v.container.querySelector('[data-testid="insert-input"]'))
    expect(v.container.querySelector('textarea').value).toBe('Review this {{input}}')
    await click(v.container.querySelector('[data-testid="insert-prev"]'))
    expect(v.container.querySelector('textarea').value).toBe('Review this {{input}} {{prev}}')
    await v.unmount()
  })
  it('an empty prompt gets just the placeholder', async () => {
    const v = await render(<H stepIndex={0} prompt="" />)
    await flush()
    await click(v.container.querySelector('[data-testid="insert-input"]'))
    expect(v.container.querySelector('textarea').value).toBe('{{input}}')
    await v.unmount()
  })
  it('offers "Result of the previous step" only from step 2 on', async () => {
    const v = await render(<H stepIndex={0} prompt="x" />)
    await flush()
    expect(v.container.querySelector('[data-testid="insert-prev"]')).toBeNull()
    expect(v.container.querySelector('[data-testid="insert-input"]').textContent).toContain('What you run it on')
    await v.unmount()
    const w = await render(<H stepIndex={1} prompt="x" />)
    await flush()
    expect(w.container.querySelector('[data-testid="insert-prev"]').textContent).toContain('Result of the previous step')
    await w.unmount()
  })
})

describe('step type selector', () => {
  it('marks the chosen type and shows its help', async () => {
    const v = await render(<Harness stage={{ name: 'g', kind: 'gate', run: ['pytest -q'], timeout_min: 60 }} />)
    await flush()
    expect(v.container.querySelector('.co-wf-kind .ant-segmented-item-selected').textContent).toContain('Run a check')
    expect(v.container.textContent).toContain('Add one command per box. They run in order and stop at the first one that fails.')
    await v.unmount()
  })
})
