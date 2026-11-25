import dayjs from 'dayjs'
import { when } from '../time'

// Claude plan limits (7c). The server's sentences carry UTC clock times; screens
// build their own sentence so the time is the viewer's local time.

// "14:05", "tomorrow 09:10", or "26 Nov, 09:10" — local time, date only when not today.
export function localWhen(iso, now = dayjs()) {
  if (!iso) return null
  const d = typeof iso === 'object' && iso.isValid ? iso : when(iso)
  if (!d.isValid()) return null
  const hm = d.format('HH:mm')
  if (d.isSame(now, 'day')) return hm
  if (d.isSame(now.add(1, 'day'), 'day')) return `tomorrow ${hm}`
  return `${d.format('D MMM')}, ${hm}`
}

const plus1 = (iso) => (iso ? when(iso).add(1, 'minute') : null)

// A banner object {kind, text, resets_at} -> the sentence to show.
export function bannerText(b, now) {
  if (!b) return null
  if (b.kind === 'limit') {
    const t = localWhen(plus1(b.resets_at), now)
    return t ? `Your Claude plan limit was reached. Work starts again at ${t}.` : b.text
  }
  const t = localWhen(b.resets_at, now)
  return t ? `Your Claude plan is close to its limit. It resets at ${t}.` : b.text
}

// A task that waits for the plan to reset.
export function pausedText(task, now = dayjs()) {
  if (!task?.not_before) return null
  const d = when(task.not_before)
  if (!d.isValid() || !d.isAfter(now)) return null
  const t = localWhen(d, now)
  return t ? `Paused — your Claude plan limit was reached. Starts again at ${t}.` : task.wait_reason || null
}

// A plan_limit task event -> one plain line.
export function eventLine(p = {}, now) {
  if (p.paused) {
    const t = localWhen(p.not_before || p.resets_at, now)
    return t ? `Paused — your Claude plan limit was reached. Starts again at ${t}.` : p.text || 'Paused — your Claude plan limit was reached.'
  }
  if (p.status === 'rejected') {
    const t = localWhen(plus1(p.resets_at), now)
    return t ? `Your Claude plan limit was reached. Work starts again at ${t}.` : 'Your Claude plan limit was reached.'
  }
  if (p.status === 'allowed_warning') {
    const t = localWhen(p.resets_at, now)
    return t ? `Your Claude plan is close to its limit. It resets at ${t}.` : 'Your Claude plan is close to its limit.'
  }
  return 'Your Claude plan is working normally.'
}

// A server sentence with a UTC "HH:MM" (a held workflow step) -> the same sentence in local time.
export function localizeServerText(text, now = dayjs()) {
  if (!text) return text
  return text.replace(/\b(\d{2}):(\d{2})\b/, (m, h, mi) => {
    const n = new Date(now.valueOf())
    let ms = Date.UTC(n.getUTCFullYear(), n.getUTCMonth(), n.getUTCDate(), +h, +mi)
    if (ms < now.valueOf() - 3600e3) ms += 86400e3
    return localWhen(dayjs(ms), now)
  })
}

export const STATUS_WORDS = {
  allowed: 'Working normally',
  allowed_warning: 'Close to the limit',
  rejected: 'Limit reached',
}

// A person's tag on the Users page, or null.
export function userTag(st, now) {
  if (!st) return null
  if (st.held) {
    const t = localWhen(plus1(st.resets_at), now)
    return { color: 'red', text: t ? `Limit reached until ${t}` : 'Limit reached' }
  }
  if (st.banner?.kind === 'warning') return { color: 'gold', text: 'Close to limit' }
  return null
}
