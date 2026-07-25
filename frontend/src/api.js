// Thin fetch wrapper around the orchestrator REST API. Relative URLs are
// proxied to the backend by Vite in dev (see vite.config.js).

async function req(method, path, body) {
  const opts = { method, headers: {} }
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json'
    opts.body = JSON.stringify(body)
  }
  const res = await fetch(path, opts)
  if (!res.ok) {
    let detail = res.statusText
    try {
      detail = (await res.json()).detail || detail
    } catch (_) {}
    throw new Error(detail)
  }
  if (res.status === 204) return null
  return res.json()
}

export const api = {
  listTasks: (params = {}) => {
    const qs = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v != null && v !== '')
    ).toString()
    return req('GET', `/tasks${qs ? `?${qs}` : ''}`)
  },
  getTask: (id) => req('GET', `/tasks/${id}`),
  createTask: (payload) => req('POST', '/tasks', payload),
  cancelTask: (id) => req('POST', `/tasks/${id}/cancel`),
  retryTask: (id) => req('POST', `/tasks/${id}/retry`),
  duplicateTask: (id) => req('POST', `/tasks/${id}/duplicate`),
  getEvents: (id, afterSeq = 0) => req('GET', `/tasks/${id}/events?after_seq=${afterSeq}`),
  getArtifacts: (id) => req('GET', `/tasks/${id}/artifacts`),
  stats: () => req('GET', '/tasks/stats'),
  artifactUrl: (id, relPath) => `/tasks/${id}/artifacts/${relPath}`,
  streamUrl: (id, lastSeq = 0) => {
    const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
    return `${proto}://${window.location.host}/tasks/${id}/stream?last_seq=${lastSeq}`
  },
}

export const STATUS_COLORS = {
  queued: 'default',
  running: 'processing',
  awaiting_approval: 'warning',
  completed: 'success',
  failed: 'error',
  cancelled: 'default',
}

export const PRIORITY_COLORS = {
  low: 'blue',
  normal: 'default',
  high: 'orange',
  urgent: 'red',
}
