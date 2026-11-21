// Thin fetch wrapper around the orchestrator REST API. Relative URLs are
// proxied to the backend by Vite in dev (see vite.config.js).

let unauthorizedHandler = null
export function setUnauthorizedHandler(fn) {
  unauthorizedHandler = fn
}
export function notifyUnauthorized() {
  unauthorizedHandler?.()
}
// 401s from these are expected (bad creds / no session yet) and handled by callers.
const NO_401_HOOK = ['/auth/login', '/auth/logout', '/auth/me', '/auth/setup', '/auth/password']

async function req(method, path, body) {
  const opts = { method, headers: { 'X-Requested-With': 'co' }, credentials: 'same-origin' }
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json'
    opts.body = JSON.stringify(body)
  }
  const res = await fetch(path, opts)
  if (res.status === 401 && !NO_401_HOOK.includes(path)) unauthorizedHandler?.()
  if (!res.ok) {
    let detail = res.statusText
    try {
      detail = (await res.json()).detail || detail
    } catch (_) {}
    // Validation errors (422) come as [{loc, msg}]: show "field: message".
    if (Array.isArray(detail) && detail.every((d) => d && d.msg))
      detail = detail.map((d) => `${(d.loc || []).filter((x) => x !== 'body').join('.') || 'request'}: ${d.msg}`).join('; ')
    const err = new Error(typeof detail === 'string' ? detail : JSON.stringify(detail))
    err.status = res.status
    throw err
  }
  if (res.status === 204) return null
  return res.json()
}

function qs(params = {}) {
  const s = new URLSearchParams(
    Object.entries(params).filter(([, v]) => v != null && v !== '')
  ).toString()
  return s ? `?${s}` : ''
}

export const api = {
  // ---- Auth & users ----
  setupStatus: () => req('GET', '/auth/setup-status'),
  setup: (payload) => req('POST', '/auth/setup', payload),
  login: (email, password) => req('POST', '/auth/login', { email, password }),
  logout: () => req('POST', '/auth/logout'),
  me: () => req('GET', '/auth/me'),
  changePassword: (current_password, new_password) =>
    req('POST', '/auth/password', { current_password, new_password }),
  listUsers: () => req('GET', '/users'),
  createUser: (payload) => req('POST', '/users', payload),
  updateUser: (id, payload) => req('PATCH', `/users/${id}`, payload),
  resetUserPassword: (id, password) => req('POST', `/users/${id}/reset-password`, { password }),
  userDirectory: () => req('GET', '/users/directory'),

  // ---- Personal credentials (write-only: responses carry presence/kind only) ----
  myCredentials: () => req('GET', '/me/credentials'),
  saveMyCredentials: (payload) => req('PUT', '/me/credentials', payload),
  removeMyClaude: () => req('DELETE', '/me/credentials/claude'),
  removeMyGithub: () => req('DELETE', '/me/credentials/github'),
  testMyCredential: (which) => req('POST', '/me/credentials/test', { which }),
  // ---- Org fallback Claude API key (admin) ----
  orgFallback: () => req('GET', '/admin/org-fallback'),
  setOrgFallback: (api_key) => req('PUT', '/admin/org-fallback', { api_key }),
  removeOrgFallback: () => req('DELETE', '/admin/org-fallback'),

  // ---- Teams (writes are admin-only) ----
  listTeams: () => req('GET', '/teams'),
  createTeam: (name) => req('POST', '/teams', { name }),
  updateTeam: (id, name) => req('PATCH', `/teams/${id}`, { name }),
  deleteTeam: (id) => req('DELETE', `/teams/${id}`),
  setTeamMembers: (id, userIds) => req('PUT', `/teams/${id}/members`, { user_ids: userIds }),

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
  deleteTask: (id) => req('DELETE', `/tasks/${id}`),
  followupTask: (id, prompt) => req('POST', `/tasks/${id}/followup`, { prompt }),
  getEvents: (id, afterSeq = 0) => req('GET', `/tasks/${id}/events?after_seq=${afterSeq}`),
  getThread: (id) => req('GET', `/tasks/${id}/thread`),
  getArtifacts: (id) => req('GET', `/tasks/${id}/artifacts`),
  stats: () => req('GET', '/tasks/stats'),
  artifactUrl: (id, relPath) => `/tasks/${id}/artifacts/${relPath}`,
  reportUrl: (id, format) => `/tasks/${id}/report?format=${format}`,

  // ---- MCP management ----
  listServers: () => req('GET', '/mcp/servers'),
  createServer: (payload) => req('POST', '/mcp/servers', payload),
  updateServer: (id, payload) => req('PATCH', `/mcp/servers/${id}`, payload),
  deleteServer: (id) => req('DELETE', `/mcp/servers/${id}`),
  testServer: (id) => req('POST', `/mcp/servers/${id}/test`),
  getPolicies: (id) => req('GET', `/mcp/servers/${id}/policies`),
  setPolicies: (id, policies) => req('PUT', `/mcp/servers/${id}/policies`, policies),
  mcpObservability: (days = 7) => req('GET', `/mcp/observability?days=${days}`),

  // ---- Projects & memory ----
  listProjects: (includeArchived = false) =>
    req('GET', `/projects?include_archived=${includeArchived}`),
  createProject: (payload) => req('POST', '/projects', payload),
  discoverProjects: () => req('GET', '/projects/discover'),
  importProjects: (directories) => req('POST', '/projects/import', { directories }),
  updateProject: (id, payload) => req('PATCH', `/projects/${id}`, payload),
  deleteProject: (id) => req('DELETE', `/projects/${id}`),
  listProjectServers: (id) => req('GET', `/projects/${id}/servers`),
  saveProjectServers: (id, servers) => req('PUT', `/projects/${id}/servers`, servers),
  getMemory: (id) => req('GET', `/projects/${id}/memory`),
  regenerateMemory: (id) => req('POST', `/projects/${id}/memory/regenerate`),
  listSummaries: (id) => req('GET', `/projects/${id}/summaries`),
  projectStats: (id) => req('GET', `/projects/${id}/stats`),
  // members: [{subject_type: 'user'|'team', subject_id, role}] — PUT replaces the list
  getProjectMembers: (id) => req('GET', `/projects/${id}/members`),
  setProjectMembers: (id, members) => req('PUT', `/projects/${id}/members`, members),

  // ---- Usage & cost ----
  usageSummary: () => req('GET', '/usage/summary'),
  usageTimeseries: (days = 30) => req('GET', `/usage/timeseries?days=${days}`),
  usageByProject: () => req('GET', '/usage/by-project'),
  usageByModel: () => req('GET', '/usage/by-model'),
  usageByUser: () => req('GET', '/usage/by-user'),

  // ---- Security findings ----
  listFindings: (params = {}) => {
    const qs = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v != null && v !== '')
    ).toString()
    return req('GET', `/findings${qs ? `?${qs}` : ''}`)
  },
  findingsSummary: (projectId) =>
    req('GET', `/findings/summary${projectId ? `?project_id=${projectId}` : ''}`),
  updateFinding: (id, payload) => req('PATCH', `/findings/${id}`, payload),

  claudeStatus: () => req('GET', '/system/claude'),

  // ---- Templates & schedules ----
  listTemplates: () => req('GET', '/templates'),
  templatePresets: () => req('GET', '/templates/presets'),
  createTemplate: (payload) => req('POST', '/templates', payload),
  deleteTemplate: (id) => req('DELETE', `/templates/${id}`),
  runTemplate: (id) => req('POST', `/templates/${id}/run`),
  listAgents: () => req('GET', '/agents'),
  agentPresets: () => req('GET', '/agents/presets'),
  createAgent: (payload) => req('POST', '/agents', payload),
  updateAgent: (id, payload) => req('PATCH', `/agents/${id}`, payload),
  deleteAgent: (id) => req('DELETE', `/agents/${id}`),
  runAgent: (id, payload) => req('POST', `/agents/${id}/run`, payload),
  bulkRunAgent: (id, payload) => req('POST', `/agents/${id}/bulk`, payload),

  // ---- Workflows (saved stage pipelines) ----
  listWorkflows: () => req('GET', '/workflows'),
  workflowPresets: () => req('GET', '/workflows/presets'),
  createWorkflow: (payload) => req('POST', '/workflows', payload),
  updateWorkflow: (id, payload) => req('PATCH', `/workflows/${id}`, payload),
  deleteWorkflow: (id) => req('DELETE', `/workflows/${id}`),
  runWorkflow: (id, payload) => req('POST', `/workflows/${id}/run`, payload),
  listWorkflowRuns: ({ limit } = {}) => req('GET', limit ? `/workflows/runs?limit=${limit}` : '/workflows/runs'),
  // Active runs + running coordinator threads (Home comets).
  liveRuns: () => req('GET', '/workflows/runs/live'),
  getWorkflowRun: (id) => req('GET', `/workflows/runs/${id}`),
  cancelWorkflowRun: (id) => req('POST', `/workflows/runs/${id}/cancel`),
  retryWorkflowRun: (id) => req('POST', `/workflows/runs/${id}/retry`),

  // ---- Skill packs (Claude Code plugins) ----
  listSkills: () => req('GET', '/skills'),
  skillPresets: () => req('GET', '/skills/presets'),
  installSkill: (payload) => req('POST', '/skills', payload),
  updateSkill: (id, payload) => req('PATCH', `/skills/${id}`, payload),
  deleteSkill: (id) => req('DELETE', `/skills/${id}`),
  // ---- Context Store (curated knowledge docs, tiered) ----
  listContextDocs: (params = {}) => req('GET', `/context-docs${qs(params)}`),
  previewContext: (projectId) => req('GET', `/context-docs/preview${qs({ project_id: projectId })}`),
  createContextDoc: (payload) => req('POST', '/context-docs', payload),
  updateContextDoc: (id, payload) => req('PATCH', `/context-docs/${id}`, payload),
  deleteContextDoc: (id) => req('DELETE', `/context-docs/${id}`),
  listSchedules: () => req('GET', '/schedules'),
  createSchedule: (payload) => req('POST', '/schedules', payload),
  updateSchedule: (id, payload) => req('PATCH', `/schedules/${id}`, payload),
  deleteSchedule: (id) => req('DELETE', `/schedules/${id}`),
  runSchedule: (id) => req('POST', `/schedules/${id}/run`),
  scheduleRuns: (id) => req('GET', `/schedules/${id}/runs`),
  previewCron: (cron) => req('GET', `/schedules/preview?cron=${encodeURIComponent(cron)}`),

  // ---- Approvals ----
  listApprovals: () => req('GET', '/approvals'),
  approveTask: (id) => req('POST', `/approvals/${id}/approve`),
  rejectTask: (id, reason) => req('POST', `/approvals/${id}/reject`, { reason }),
  bulkApprove: (taskIds) => req('POST', '/approvals/approve', { task_ids: taskIds }),

  // ---- co-guard action approvals (a 409 "expired" means the request expired) ----
  // Pending requests the viewer may decide (each carries can_decide).
  listActionApprovals: () => req('GET', '/guard/approvals?status=pending'),
  // Every request of one task (run log enrichment).
  taskActionApprovals: (taskId) => req('GET', `/guard/approvals${qs({ task_id: taskId, status: 'all' })}`),
  getActionApproval: (id) => req('GET', `/guard/approvals/${encodeURIComponent(id)}`),
  // scope: "once" (this call) | "run" (these rules, this thread, 12 h).
  approveAction: (id, scope = 'once') =>
    req('POST', `/guard/approvals/${encodeURIComponent(id)}/approve`, { scope }),
  rejectAction: (id, reason) => req('POST', `/guard/approvals/${encodeURIComponent(id)}/reject`, { reason: reason || null }),

  // Run grants (approve-for-this-run); without rootId: every grant the viewer can see.
  listGrants: (rootId) => req('GET', `/guard/grants${qs({ root_id: rootId })}`),
  revokeGrant: (id) => req('POST', `/guard/grants/${encodeURIComponent(id)}/revoke`),

  // ---- co-guard action policy (Phase 2) ----
  // Org rules, or a project's rules with projectId.
  listGuardRules: (projectId) => req('GET', `/guard/rules${qs({ project_id: projectId })}`),
  createGuardRule: (payload) => req('POST', '/guard/rules', payload),
  updateGuardRule: (id, payload) => req('PATCH', `/guard/rules/${encodeURIComponent(id)}`, payload),
  deleteGuardRule: (id) => req('DELETE', `/guard/rules/${encodeURIComponent(id)}`),
  guardBuiltins: () => req('GET', '/guard/builtins'),
  guardOrgSettings: () => req('GET', '/guard/org-settings'),
  guardHealth: () => req('GET', '/guard/health'),
  saveGuardOrgSettings: (payload) => req('PUT', '/guard/org-settings', payload),
  projectRulesFile: (id) => req('GET', `/projects/${encodeURIComponent(id)}/guard/rules-file`),

  streamUrl: (id, lastSeq = 0) => {
    const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
    return `${proto}://${window.location.host}/tasks/${id}/stream?last_seq=${lastSeq}`
  },
}

// "claude-haiku-4-5-20251001" -> "haiku-4-5"; null/undefined -> null
export function shortModel(s) {
  if (!s) return null
  return s.replace(/^claude-/, '').replace(/-\d{8}$/, '')
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

export const RISK_COLORS = {
  info: 'blue',
  warning: 'orange',
  critical: 'red',
}

export const SEVERITY_COLORS = {
  critical: 'red',
  high: 'volcano',
  medium: 'gold',
  low: 'blue',
  info: 'default',
}

export const FINDING_STATUS_COLORS = {
  open: 'red',
  fixed: 'green',
  accepted: 'blue',
  false_positive: 'default',
}

export const POLICY_ACTION_COLORS = {
  auto_approve: 'success',
  require_approval: 'warning',
  block: 'error',
}
