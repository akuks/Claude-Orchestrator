import dayjs from 'dayjs'

// Backend timestamps are UTC; SQLite returns them without an offset, which
// dayjs/Date would read as local time. Treat an offset-less ISO time as UTC.
export function parseTime(d) {
  if (!d) return null
  const s = typeof d === 'string' && /T\d{2}:\d{2}(:\d{2}(\.\d+)?)?$/.test(d) ? `${d}Z` : d
  const t = new Date(s).getTime()
  return Number.isFinite(t) ? t : null
}

// dayjs for a backend timestamp (anything else goes straight to dayjs).
export const when = (d) => dayjs(typeof d === 'string' ? (parseTime(d) ?? d) : d)
