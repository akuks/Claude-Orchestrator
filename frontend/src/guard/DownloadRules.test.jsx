import { act } from 'react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { flush, render } from '../home/testRender'

vi.mock('../api', () => ({ api: { exportRules: vi.fn(async () => '# rules') } }))

import { api } from '../api'
import DownloadRules from './DownloadRules'

let view
beforeAll(() => {
  window.matchMedia ||= () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} })
})
afterEach(async () => {
  await view?.unmount()
  view = null
})

describe('DownloadRules', () => {
  it('fetches the export for the project and saves it as rules.md', async () => {
    URL.createObjectURL = vi.fn(() => 'blob:x')
    URL.revokeObjectURL = vi.fn()
    let name
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function () { name = this.download })
    view = await render(<DownloadRules projectId="p1" />)
    const b = view.container.querySelector('[data-testid="download-rules"]')
    expect(b.textContent).toBe('Download the rules as a file')
    await act(async () => { b.dispatchEvent(new MouseEvent('click', { bubbles: true })) })
    await flush()
    expect(api.exportRules).toHaveBeenCalledWith('p1')
    expect(click).toHaveBeenCalled()
    expect(name).toBe('rules.md')
    click.mockRestore()
  })
})
