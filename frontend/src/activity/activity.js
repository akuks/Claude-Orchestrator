// Pure helpers for the project Activity tab.
const plural = (n, one, many) => (n === 1 ? one : many)

// "+12 −3 in 4 files"
export const changeLine = (c) => `+${c.added} −${c.removed} in ${c.files} ${plural(c.files, 'file', 'files')}`

// [{date: 'YYYY-MM-DD', commits: []}], newest day first (commits keep the server's order).
export function groupByDay(commits = []) {
  const m = new Map()
  for (const c of commits) {
    const d = String(c.date).slice(0, 10)
    if (!m.has(d)) m.set(d, [])
    m.get(d).push(c)
  }
  return [...m.entries()].sort((a, b) => (a[0] < b[0] ? 1 : -1)).map(([date, commits]) => ({ date, commits }))
}
