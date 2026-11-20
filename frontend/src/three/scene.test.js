import { describe, expect, it } from 'vitest'
import { palette } from '../theme/tokens'
import { rgb, sceneColors } from './scene'

const same = (v, hex) => {
  const w = rgb(hex)
  expect([v.x, v.y, v.z]).toEqual([w.x, w.y, w.z])
}

describe('sceneColors come from the theme tokens', () => {
  it.each(['light', 'night'])('%s: stars, flow, spark and dust use palette tokens', (mode) => {
    const p = palette[mode]
    const c = sceneColors(mode)
    same(c.next, p.starNext)
    same(c.flow, p.sceneFlow)
    same(c.sparkCore, p.sparkCore)
    expect(c.dust).toEqual(p.sceneDust)
  })
  it('night dust adds the night text colour to the sunset trio', () => {
    expect(palette.light.sceneDust).toEqual([palette.amber, palette.orange, palette.rose])
    expect(palette.night.sceneDust).toEqual([palette.amber, palette.orange, palette.rose, palette.night.text])
  })
})

describe('fillDust (particle field)', () => {
  it('refills the same attributes in place, so resizes and mode switches leak no GPU buffers', async () => {
    const { BufferGeometry } = await import('three')
    const { fillDust } = await import('./scene')
    const geo = new BufferGeometry()
    const size = { W: 20, H: 10, dist: 20 }
    fillDust(geo, 50, size, sceneColors('light'))
    const before = { ...geo.attributes }
    const versions = Object.fromEntries(Object.entries(before).map(([k, a]) => [k, a.version]))
    expect(Object.keys(before).sort()).toEqual(['aColor', 'aPhase', 'aSize', 'position'])
    expect(before.position.count).toBe(50)
    fillDust(geo, 50, { W: 40, H: 12, dist: 25 }, sceneColors('night'))
    for (const k of Object.keys(before)) {
      expect(geo.attributes[k]).toBe(before[k])
      expect(geo.attributes[k].version).toBeGreaterThan(versions[k])
    }
  })
  it('is deterministic for the same size and colours', async () => {
    const { BufferGeometry } = await import('three')
    const { fillDust } = await import('./scene')
    const a = new BufferGeometry()
    const b = new BufferGeometry()
    const size = { W: 20, H: 10, dist: 20 }
    fillDust(a, 30, size, sceneColors('night'))
    fillDust(b, 30, size, sceneColors('night'))
    expect(Array.from(a.attributes.position.array)).toEqual(Array.from(b.attributes.position.array))
  })
})
