import { describe, expect, it } from 'vitest'
import { TIER_OPTIONS, isModelId, modelOptions } from './tiers'

describe('tiers', () => {
  it('lists the three tiers', () => {
    expect(TIER_OPTIONS.map((t) => t.value)).toEqual(['strongest', 'standard', 'light'])
  })
  it('modelOptions adds the tier models to opus, sonnet, haiku, deduped', () => {
    const o = modelOptions({ strongest: 'claude-opus-4-1', standard: 'sonnet', light: 'haiku' })
    expect(o.map((x) => x.value)).toEqual(['sonnet', 'opus', 'haiku', 'claude-opus-4-1'])
  })
  it('modelOptions without a map gives the three', () => {
    expect(modelOptions(null)).toHaveLength(3)
  })
  it('isModelId follows the backend regex', () => {
    expect(isModelId('claude-haiku-4-5')).toBe(true)
    expect(isModelId('claude-opus-4-1[1m]')).toBe(true)
    expect(isModelId('gpt-4')).toBe(false)
    expect(isModelId('claude-' + 'x'.repeat(30))).toBe(false)
  })
})
