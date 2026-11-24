// Plain sentences for failed calls: never the server's raw text, status codes or field names.
export const NOT_ALLOWED = "You don't have permission to change this."
export const BRAND_HELP = 'Use letters, numbers, dots, dashes, underscores or colons only (up to 120).'
export const TRY_AGAIN = "That didn't work. Try again."

export function plainError(e, { brand = false } = {}) {
  if (e?.status === 403 || /\b403\b|forbidden/i.test(e?.message || '')) return NOT_ALLOWED
  if (brand && e?.status === 422) return BRAND_HELP
  return TRY_AGAIN
}
