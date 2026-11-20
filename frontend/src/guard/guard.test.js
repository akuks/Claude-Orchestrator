import { describe, expect, it, vi } from 'vitest'
import {
  DECIDE_MARGIN_MS,
  GUARD_SETTINGS_HELP,
  RULE_NAMES,
  canEditGuard,
  closesIn,
  formatLeft,
  formatWait,
  guardLine,
  pendingApprovalTotal,
  ruleName,
} from './guard'

describe('rule names', () => {
  it('has a friendly name for every co-guard rule id', () => {
    for (const id of [
      'rm-rf-outside', 'git-force-push-main', 'git-reset-hard', 'git-clean-fdx', 'git-stash-drop', 'secret-files',
      'chmod-wide', 'pipe-to-shell', 'self-protect', 'mcp-require-approval', 'mcp-block', 'git-internals',
      'agent-config', 'uncertain-command', 'unparseable', 'unsupported-shell', 'unknown-tool',
    ]) {
      expect(RULE_NAMES[id], id).toBeTruthy()
      expect(ruleName(id)).not.toBe(id)
    }
  })
  it('falls back to the id for an unknown rule', () => {
    expect(ruleName('new-rule')).toBe('new-rule')
  })
})

describe('countdown', () => {
  it('formats the time left as m:ss', () => {
    expect(formatLeft(125_000)).toBe('2:05')
    expect(formatLeft(59_400)).toBe('0:59')
    expect(formatLeft(0)).toBe('0:00')
    expect(formatLeft(-5)).toBe('0:00')
  })
  it('counts down to when decisions close (5 s before expiry)', () => {
    const now = Date.parse('2026-11-21T10:00:00Z')
    expect(DECIDE_MARGIN_MS).toBe(5000)
    expect(closesIn({ expires_at: '2026-11-21T10:01:05Z' }, now)).toBe(60_000)
    // SQLite drops the offset: a bare ISO time is UTC
    expect(closesIn({ expires_at: '2026-11-21T10:01:05' }, now)).toBe(60_000)
  })
  it('formats the approval wait with minutes', () => {
    expect(formatWait(300)).toBe('5 min')
    expect(formatWait(90)).toBe('1 min 30 s')
    expect(formatWait(60)).toBe('1 min')
    expect(formatWait(900)).toBe('15 min')
  })
})

describe('settings help', () => {
  it('says what can be turned off and what always applies', () => {
    expect(GUARD_SETTINGS_HELP).toMatch(/built-in .*rules/i)
    expect(GUARD_SETTINGS_HELP).toMatch(/MCP/)
    expect(GUARD_SETTINGS_HELP).toMatch(/self-protection/i)
    expect(GUARD_SETTINGS_HELP).toMatch(/always apply/i)
    expect(GUARD_SETTINGS_HELP).toContain('!`cmd`')
    expect(GUARD_SETTINGS_HELP).toMatch(/skill packs/)
    expect(GUARD_SETTINGS_HELP).toMatch(/disabled/)
  })
})

describe('guardLine', () => {
  const names = new Map([['u2', 'Bea']])
  const approvals = new Map([
    ['r1', { id: 'r1', status: 'approved', decided_by: 'u2' }],
    ['r2', { id: 'r2', status: 'rejected', decided_by: 'u2', decision_reason: 'not now' }],
  ])
  const ctx = { approvals, names }

  it('hard block', () => {
    const l = guardLine({ event: 'PreToolUse', decision: 'deny', tool: 'Bash', rule_ids: ['rm-rf-outside'],
      summary: 'rm -rf ~', reason: 'this would delete your home folder (rm-rf-outside)' }, ctx)
    expect(l.kind).toBe('blocked')
    expect(l.text).toContain('Bash')
    expect(l.text).toContain('rm -rf ~')
    expect(l.text).toContain('Delete outside the project')
  })
  it('approved, by whom', () => {
    const l = guardLine({ event: 'PreToolUse', decision: 'approved', status: 'approved', request_id: 'r1',
      tool: 'Bash', summary: 'git reset --hard', rule_ids: ['git-reset-hard'] }, ctx)
    expect(l.kind).toBe('approved')
    expect(l.text).toContain('approved by Bea')
    expect(l.requestId).toBe('r1')
  })
  it('approved without enrichment', () => {
    expect(guardLine({ event: 'PreToolUse', decision: 'approved', status: 'approved', request_id: 'zz' }).text)
      .toContain('approved by a person')
  })
  it('rejected, with the reason', () => {
    const l = guardLine({ event: 'PreToolUse', decision: 'deny', status: 'rejected', request_id: 'r2', tool: 'Bash',
      summary: 'git clean -fdx' }, ctx)
    expect(l.kind).toBe('rejected')
    expect(l.text).toContain('rejected by Bea')
    expect(l.text).toContain('not now')
  })
  it.each(['expired', 'timeout'])('%s → expired', (status) => {
    const l = guardLine({ event: 'PreToolUse', decision: 'deny', status, request_id: 'r9', tool: 'Bash', summary: 'x' })
    expect(l.kind).toBe('expired')
  })
  it('refused filing → error', () => {
    const l = guardLine({ event: 'PreToolUse', decision: 'deny', status: 'refused', detail: 'approval API unreachable' })
    expect(l.kind).toBe('error')
    expect(l.text).toContain('approval API unreachable')
  })
  it('co-guard error', () => {
    expect(guardLine({ event: 'PreToolUse', decision: 'deny', rule_ids: [], reason: 'co-guard error' }).kind).toBe('error')
    expect(guardLine({ event: 'PreToolUse', decision: 'deny', rule_ids: [], reason: 'hook watchdog' }).kind).toBe('error')
  })
  it('settings override (R16)', () => {
    const l = guardLine({ event: 'settings_override', reason: 'project settings switch hooks off',
      files: ['.claude/settings.json'] })
    expect(l.kind).toBe('override')
    expect(l.text).toContain('.claude/settings.json')
  })
  it('watchdog stop', () => {
    const l = guardLine({ event: 'watchdog', decision: 'killed', reason: 'Bash call t1 ran without a co-guard check' })
    expect(l.kind).toBe('watchdog')
    expect(l.text).toContain('stopped')
  })
  it('unchecked error, approved call ran, config change, lifecycle', () => {
    expect(guardLine({ event: 'unchecked_error', tool: 'Bash', reason: 'a call rejected before hooks ran' }).kind).toBe('info')
    expect(guardLine({ event: 'PostToolUse', decision: 'ran', tool: 'Bash' }).kind).toBe('ran')
    expect(guardLine({ event: 'ConfigChange', decision: 'blocked', source: 'project' }).kind).toBe('blocked')
    expect(guardLine({ event: 'SessionStart', source: 'startup' }).kind).toBe('info')
  })
})

describe('pendingApprovalTotal (rail badge / Home count)', () => {
  it('sums task approvals and action approvals', async () => {
    const api = { listApprovals: vi.fn(async () => [{}, {}]), listActionApprovals: vi.fn(async () => [{}, {}, {}]) }
    expect(await pendingApprovalTotal(api)).toBe(5)
  })
  it('counts what loaded when one source fails', async () => {
    const api = { listApprovals: vi.fn(async () => [{}]), listActionApprovals: vi.fn(async () => { throw new Error('x') }) }
    expect(await pendingApprovalTotal(api)).toBe(1)
  })
  it('null when both fail (keep the old count)', async () => {
    const boom = async () => { throw new Error('x') }
    expect(await pendingApprovalTotal({ listApprovals: boom, listActionApprovals: boom })).toBeNull()
  })
})

describe('canEditGuard', () => {
  it('maintainers and admins edit; contributors and viewers read', () => {
    expect(canEditGuard({ my_role: 'maintainer' }, { role: 'member' })).toBe(true)
    expect(canEditGuard({ my_role: null }, { role: 'admin' })).toBe(true)
    expect(canEditGuard({ my_role: 'contributor' }, { role: 'member' })).toBe(false)
    expect(canEditGuard({ my_role: 'viewer' }, { role: 'member' })).toBe(false)
  })
})
