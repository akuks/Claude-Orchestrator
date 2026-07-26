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
  followupTask: (id, prompt) => req('POST', `/tasks/${id}/followup`, { prompt }),
  getEvents: (id, afterSeq = 0) => req('GET', `/tasks/${id}/events?after_seq=${afterSeq}`),
  getThread: (id) => req('GET', `/tasks/${id}/thread`),
  getArtifacts: (id) => req('GET', `/tasks/${id}/artifacts`),
  stats: () => req('GET', '/tasks/stats'),
  artifactUrl: (id, relPath) => `/tasks/${id}/artifacts/${relPath}`,

  // ---- MCP management ----
  listServers: () => req('GET', '/mcp/servers'),
  createServer: (payload) => req('POST', '/mcp/servers', payload),
  updateServer: (id, payload) => req('PATCH', `/mcp/servers/${id}`, payload),
  deleteServer: (id) => req('DELETE', `/mcp/servers/${id}`),
  testServer: (id) => req('POST', `/mcp/servers/${id}/test`),
  getPolicies: (id) => req('GET', `/mcp/servers/${id}/policies`),
  setPolicies: (id, policies) => req('PUT', `/mcp/servers/${id}/policies`, policies),
  mcpObservability: (days = 7) => req('GET', `/mcp/observability?days=${days}`),
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

export const MCP_STATUS_COLORS = {
  healthy: 'success',
  degraded: 'warning',
  disconnected: 'error',
  unknown: 'default',
}

export const POLICY_ACTION_COLORS = {
  auto_approve: 'success',
  require_approval: 'warning',
  block: 'error',
}
