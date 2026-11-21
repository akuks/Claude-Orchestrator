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

// ---- Phase 2 ----
import {
  HIDDEN_CHARS,
  ONCE_ONLY_REASON,
  QUARANTINE_GRANT_HELP,
  READONLY_HELP,
  SEATBELT_HELP,
  TRUST_HELP,
  NO_ANSWER_HELP,
  canApproveForRun,
  grantCovers,
  revealHidden,
  runGrantTooltip,
  waitingLine,
} from './guard'

const KEY = 'a'.repeat(64)

describe('phase 2 rule names', () => {
  it('names the new built-ins and custom rule ids', () => {
    for (const id of ['sql-destroy', 'db-write', 'guest-quarantine', 'readonly-source']) expect(ruleName(id)).not.toBe(id)
    expect(ruleName('org.no-prod-deploys')).toBe('Org rule no-prod-deploys')
    expect(ruleName('project.x')).toBe('Project rule x')
    expect(ruleName('repo.y')).toBe('Repo rule y')
    expect(ruleName('weird.z')).toBe('weird.z')
  })
})

describe('revealHidden', () => {
  it('makes every bidi and zero-width character visible and counts them', () => {
    const chars = ['‪', '‮', '⁦', '⁩', '​', '‏', '⁠', '⁤', '﻿']
    const { text, count } = revealHidden(`rm${chars.join('')}x`)
    expect(count).toBe(chars.length)
    expect(text).toContain('⟦U+202E⟧')
    expect(text).toContain('⟦U+FEFF⟧')
    expect(text).toContain('⟦U+200B⟧')
    expect(text.match(HIDDEN_CHARS)).toBeNull()
  })
  it('leaves ordinary text alone', () => {
    expect(revealHidden('ls -la ü 😀')).toEqual({ text: 'ls -la ü 😀', count: 0 })
    expect(revealHidden(null)).toEqual({ text: '', count: 0 })
  })
})

describe('approve for this run', () => {
  it('is offered when every command-keyed rule has the verified key', () => {
    expect(canApproveForRun({ run_keys: [{ rule_id: 'git-reset-hard', key_kind: 'command', key: KEY }] })).toBe(true)
    expect(canApproveForRun({ run_keys: [{ rule_id: 'guest-quarantine', key_kind: 'rule', key: '*' }] })).toBe(true)
    expect(canApproveForRun({ run_keys: [{ rule_id: 'mcp-require-approval', key_kind: 'tool', key: 'mcp__db__q' }] })).toBe(true)
  })
  it('is not offered for agent-filed / cut requests (no key) or when nothing is grantable', () => {
    expect(canApproveForRun({ run_keys: [{ rule_id: 'git-reset-hard', key_kind: 'command', key: '' }] })).toBe(false)
    expect(canApproveForRun({ run_keys: [] })).toBe(false)
    expect(canApproveForRun({})).toBe(false)
    expect(
      canApproveForRun({
        run_keys: [
          { rule_id: 'guest-quarantine', key_kind: 'rule', key: '*' },
          { rule_id: 'secret-files', key_kind: 'command', key: '' },
        ],
      }),
    ).toBe(false)
    expect(ONCE_ONLY_REASON).toMatch(/can't be approved for the whole run/)
  })
  it('tooltips say what the grant covers', () => {
    expect(runGrantTooltip({ run_keys: [{ rule_id: 'git-reset-hard', key_kind: 'command', key: KEY }] })).toBe(
      'These rules, this thread, 12 h (Discard uncommitted work). Bash and Monitor runs of the same command share one approval',
    )
    expect(runGrantTooltip({ run_keys: [{ rule_id: 'guest-quarantine', key_kind: 'rule', key: '*' }] })).toBe(
      QUARANTINE_GRANT_HELP,
    )
    expect(QUARANTINE_GRANT_HELP).toBe("Lets this repo's code run in this thread for 12 h")
    expect(grantCovers({ key_kind: 'tool', key: 'mcp__db__q' })).toBe('the tool mcp__db__q')
    expect(grantCovers({ key_kind: 'rule', rule_id: 'guest-quarantine' })).toBe("all of this repo's code")
    expect(grantCovers({ key_kind: 'command' })).toBe('the same command')
  })
})

describe('phase 2 help texts', () => {
  it('carry the owed hints', () => {
    expect(SEATBELT_HELP).toMatch(/seatbelt, not a boundary/)
    expect(SEATBELT_HELP).toMatch(/reading \.env/)
    expect(READONLY_HELP).toMatch(/formatters on "\."/)
    expect(READONLY_HELP).toMatch(/src/)
    expect(READONLY_HELP).toMatch(/exclude/)
    expect(TRUST_HELP).toMatch(/commit authors' emails/)
    expect(TRUST_HELP).toMatch(/git email/)
    expect(NO_ANSWER_HELP.deny_and_resume).toMatch(/24 h/)
    expect(NO_ANSWER_HELP.deny_and_resume).toMatch(/cancelled/)
  })
})

describe('guardLine: phase 2 records', () => {
  it('granted: allowed by an earlier approval', () => {
    const l = guardLine({ event: 'PreToolUse', decision: 'granted', grant_ids: ['g1'], tool: 'Bash', summary: 'git reset --hard', rule_ids: ['git-reset-hard'] })
    expect(l.kind).toBe('granted')
    expect(l.text).toBe('Bash: git reset --hard [Discard uncommitted work] — allowed by an earlier approval (grant g1)')
  })
  it('filed: the agent stopped; the request waits for a person', () => {
    const ctx = { approvals: new Map([['r9', { id: 'r9', can_decide: true }]]) }
    const l = guardLine({ event: 'PreToolUse', decision: 'deny', status: 'filed', request_id: 'r9', tool: 'Bash', summary: 'x', rule_ids: [] }, ctx)
    expect(l.kind).toBe('filed')
    expect(l.requestId).toBe('r9')
    expect(l.canDecide).toBe(true)
    expect(l.text).toMatch(/filed for a person; the agent stopped/)
  })
  it('policy events at task start', () => {
    expect(guardLine({ event: 'references', count: 2 }).text).toBe('co-guard: 2 reference projects mounted read-only')
    expect(guardLine({ event: 'reference_skipped', project_id: 'p', reason: 'gone' }).kind).toBe('override')
    const rf = guardLine({ event: 'rules_file', path: '/r', errors: [{ line: 3, message: 'disable: lines are ignored' }] })
    expect(rf.text).toContain('line 3: disable: lines are ignored')
  })
  it('a resume-phase waiting line says the agent stopped', () => {
    expect(waitingLine({ id: 'r', delivery: 'resume', can_decide: true, tool: 'Bash', summary: 'x' }).text).toMatch(/agent stopped/)
  })
})

describe('actionTitle reveals hidden characters', () => {
  it('Home rows and waiting lines cannot be reordered by bidi controls', async () => {
    const { actionTitle } = await import('./guard')
    expect(actionTitle({ tool: 'Bash', summary: 'a\u202eb' })).toBe('Bash: a⟦U+202E⟧b')
  })
})

describe('fix round 1: wider hidden-character set', () => {
  it('reveals C0 (not tab/newline), C1, separators, fillers and tag characters', () => {
    const chars = ['\u0000', '\u0007', '\u000B', '\u001B', '\u007F', '\u0085', '\u009F', '\u2028', '\u2029', '\u00AD',
      '\u034F', '\u115F', '\u3164', '\uFFA0', '\u180E', '\u061C', '\u{E0041}', '\u{E007F}', '\u{E0000}']
    for (const ch of chars) {
      const { text, count } = revealHidden(`a${ch}b`)
      expect(count, ch.codePointAt(0).toString(16)).toBe(1)
      expect(text).toBe(`a⟦U+${ch.codePointAt(0).toString(16).toUpperCase().padStart(4, '0')}⟧b`)
    }
    expect(revealHidden('\u{E0041}').text).toBe('⟦U+E0041⟧') // one marker per tag character, not per surrogate
  })
  it('keeps tab, newline and ordinary astral characters', () => {
    expect(revealHidden('a\tb\nc 😀 \u{1F600}')).toEqual({ text: 'a\tb\nc 😀 \u{1F600}', count: 0 })
  })
  it('the run-grant tooltip says Bash and Monitor share one approval (command keys only)', () => {
    expect(runGrantTooltip({ run_keys: [{ rule_id: 'db-write', key_kind: 'command', key: KEY }] })).toMatch(
      /Bash and Monitor runs of the same command share one approval/,
    )
    expect(runGrantTooltip({ run_keys: [{ rule_id: 'mcp-require-approval', key_kind: 'tool', key: 'mcp__x__y' }] })).not.toMatch(/Monitor/)
  })
  it('a tool grant key is revealed', () => {
    expect(grantCovers({ key_kind: 'tool', key: 'mcp__a\u202e__b' })).toBe('the tool mcp__a⟦U+202E⟧__b')
  })
})
