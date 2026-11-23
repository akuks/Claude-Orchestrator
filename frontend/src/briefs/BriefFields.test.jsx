import { act } from 'react'
import { Form } from 'antd'
import { afterEach, beforeAll, describe, expect, it } from 'vitest'
import { flush, render } from '../home/testRender'
import BriefFields from './BriefFields'

beforeAll(() => {
  window.matchMedia ||= () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} })
})
let view
let formRef
function Host() {
  const [form] = Form.useForm()
  formRef = form
  return (
    <Form form={form}>
      <BriefFields />
    </Form>
  )
}
afterEach(async () => {
  await view?.unmount()
  view = null
})

describe('BriefFields', () => {
  it('is collapsed by default', async () => {
    view = await render(<Host />)
    await flush()
    const item = view.container.querySelector('.ant-collapse-item')
    expect(item.classList.contains('ant-collapse-item-active')).toBe(false)
    expect(view.container.textContent).toContain('Brief')
  })
  it('expands to the five fields and More', async () => {
    view = await render(<Host />)
    await flush()
    await act(async () => view.container.querySelector('.ant-collapse-header').click())
    await flush()
    const t = view.container.textContent
    for (const l of ['Goal', 'CAN change', 'CANNOT change', 'Done', 'Stop', 'More']) expect(t).toContain(l)
  })
  it('adds a tag to CAN change', async () => {
    view = await render(<Host />)
    await flush()
    await act(async () => view.container.querySelector('.ant-collapse-header').click())
    await flush()
    const input = view.container.querySelector('#brief_can_change')
    await act(async () => {
      input.focus()
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set
      setter.call(input, 'src/**')
      input.dispatchEvent(new Event('input', { bubbles: true }))
    })
    await act(async () => {
      input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', keyCode: 13, which: 13, bubbles: true }))
    })
    await flush()
    expect(formRef.getFieldValue(['brief', 'can_change'])).toEqual(['src/**'])
  })
})
