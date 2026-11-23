// Org model tiers (mirrors backend/app/tiers.py).
export const TIER_OPTIONS = [
  { value: 'strongest', label: 'Strongest' },
  { value: 'standard', label: 'Standard' },
  { value: 'light', label: 'Light' },
]
export const TIER_KEYS = TIER_OPTIONS.map((t) => t.value)
export const DEFAULT_TIERS = { strongest: 'opus', standard: 'sonnet', light: 'haiku' }

const BASE = [
  { value: 'sonnet', label: 'Sonnet' },
  { value: 'opus', label: 'Opus' },
  { value: 'haiku', label: 'Haiku' },
]
const MODEL_ID = /^claude-[a-z0-9][a-z0-9.-]{1,60}(\[1m\])?$/

// A model id the backend accepts: opus/sonnet/haiku or claude-… (at most 32 characters).
export const isModelId = (text) =>
  typeof text === 'string' && (BASE.some((b) => b.value === text) || (text.length <= 32 && MODEL_ID.test(text)))

export function modelOptions(modelTiers) {
  const out = [...BASE]
  for (const k of TIER_KEYS) {
    const v = modelTiers?.[k]
    if (v && !out.some((o) => o.value === v)) out.push({ value: v, label: v })
  }
  return out
}
