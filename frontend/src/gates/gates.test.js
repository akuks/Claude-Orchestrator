import { describe, expect, it } from 'vitest'
import { appendPlaceholder, budgetPayload, earlierOutputs, plainServerText, checkCommand, exportFileName, formStage, runStatusText, runStepText, stageLabel, stagePayload } from './gates'

describe('checkCommand', () => {
  it('accepts a plain command', () => expect(checkCommand('pytest -q')).toBeNull())
  it('names the offending character', () => {
    expect(checkCommand('a && b')).toBe('a command cannot contain &')
    expect(checkCommand('a; b')).toBe('a command cannot contain ;')
    expect(checkCommand('echo `x`')).toBe('a command cannot contain a backtick')
    expect(checkCommand('echo $(x)')).toBe('a command cannot contain $(')
    expect(checkCommand('a\nb')).toBe('a command cannot contain a newline')
    expect(checkCommand('a\u0007b')).toBe('a command cannot contain the control character U+0007')
  })
  it('rejects empty and long', () => {
    expect(checkCommand('  ')).toBe('a command cannot be empty')
    expect(checkCommand('x'.repeat(501))).toBe('a command can be at most 500 characters')
  })
})

describe('stage helpers', () => {
  it('agent stages save without a kind key', () => {
    const p = stagePayload({ ...formStage({ name: 'Dev', items: [{ agent: 'a', prompt: 'p' }] }) })
    expect(p).toEqual({ name: 'Dev', items: [{ agent: 'a', prompt: 'p' }] })
    expect('kind' in p).toBe(false)
  })
  it('labels', () => {
    expect(stageLabel({ name: 'T', kind: 'gate' })).toBe('⚙ T')
    expect(stageLabel({ name: 'OK', kind: 'approval' })).toBe('✋ OK')
    expect(stageLabel({ name: 'Dev' })).toBe('Dev')
  })
})

describe('exportFileName', () => {
  it('replaces unsafe characters and falls back when empty', () => {
    expect(exportFileName('My flow v1.2')).toBe('My flow v1.2.md')
    expect(exportFileName('../a/b:c"d')).toBe('.._a_b_c_d.md')
    expect(exportFileName('')).toBe('workflow.md')
    expect(exportFileName('   ')).toBe('workflow.md')
    expect(exportFileName(null)).toBe('workflow.md')
  })
})

describe('plain run words', () => {
  it('maps every run status to plain words', () => {
    expect(runStatusText('pending')).toBe('Starting')
    expect(runStatusText('running')).toBe('Running')
    expect(runStatusText('paused')).toBe('Waiting for you')
    expect(runStatusText('completed')).toBe('Done')
    expect(runStatusText('failed')).toBe('Failed')
    expect(runStatusText('some_new_state')).toBe('Some new state')
    expect(runStatusText(undefined)).toBe('Unknown')
  })
  it('says "step N of M"', () => {
    expect(runStepText({ current_stage: 1, stages: [{}, {}, {}, {}, {}] })).toBe('step 2 of 5')
    expect(runStepText({ current_stage: 9, stages: [{}, {}] })).toBe('step 2 of 2')
    expect(runStepText({ current_stage: 0, stages: [] })).toBe('')
  })
})

describe('appendPlaceholder', () => {
  it('adds at the end with one space', () => {
    expect(appendPlaceholder('', '{{input}}')).toBe('{{input}}')
    expect(appendPlaceholder(undefined, '{{input}}')).toBe('{{input}}')
    expect(appendPlaceholder('Do this', '{{prev}}')).toBe('Do this {{prev}}')
    expect(appendPlaceholder('Do this\n', '{{prev}}')).toBe('Do this\n{{prev}}')
  })
})

describe('plainServerText', () => {
  it('says check where the server says gate', () => {
    expect(plainServerText('Step Tests (gate) passed, flaky')).toBe('Step Tests (check) passed, flaky')
    expect(plainServerText('Gate Tests failed: x.')).toBe('Check Tests failed: x.')
    expect(plainServerText('Step Sign (approval) approved by a')).toBe('Step Sign (approval) approved by a')
  })
})

describe('phase 5c payload', () => {
  it('an old agent step saves byte for byte as before', () => {
    const old = { name: 'Dev', items: [{ agent: 'a', prompt: 'p' }, { agent: 'b', prompt: 'q' }] }
    expect(JSON.stringify(stagePayload(formStage(old)))).toBe(JSON.stringify(old))
    const withBefore = { name: 'Dev', items: [{ agent: 'a', prompt: 'p' }], before: ['port 5432 open'] }
    expect(JSON.stringify(stagePayload(formStage(withBefore)))).toBe(JSON.stringify(withBefore))
  })
  it('an old check step saves byte for byte as before', () => {
    const old = { name: 'T', kind: 'gate', before: [], project: 'p1', run: ['pytest -q'], timeout_min: 60 }
    expect(JSON.stringify(stagePayload(formStage(old)))).toBe(JSON.stringify(old))
  })
  it('outputs only when an agent has names; auto_retry only when on', () => {
    const p = stagePayload({
      name: 'Plan',
      kind: 'agent',
      auto_retry: true,
      items: [{ agent: 'a', prompt: 'p', outputs: ['plan_path', ' summary '] }, { agent: 'b', prompt: 'q', outputs: [] }],
    })
    expect(p).toEqual({
      name: 'Plan',
      auto_retry: true,
      items: [{ agent: 'a', prompt: 'p', outputs: ['plan_path', 'summary'] }, { agent: 'b', prompt: 'q' }],
    })
    const off = stagePayload({ name: 'Plan', kind: 'agent', auto_retry: false, fix: { agent: 'x' }, items: [{ agent: 'a', prompt: 'p' }] })
    expect(off).toEqual({ name: 'Plan', items: [{ agent: 'a', prompt: 'p' }] })
  })
  it('fix on a check step only when an agent is chosen; tries default to 1', () => {
    const base = { name: 'T', kind: 'gate', project: 'p1', run: ['pytest -q'], timeout_min: 60 }
    expect(stagePayload({ ...base, fix: { agent: 'ag1', rounds: 3 } }).fix).toEqual({ agent: 'ag1', rounds: 3 })
    expect(stagePayload({ ...base, fix: { agent: 'ag1' } }).fix).toEqual({ agent: 'ag1', rounds: 1 })
    expect('fix' in stagePayload({ ...base, fix: { agent: undefined, rounds: 2 } })).toBe(false)
    expect('fix' in stagePayload(base)).toBe(false)
    // an "Ask a person" step never carries fix or auto_retry
    const ap = stagePayload({ name: 'OK', kind: 'approval', message: 'm', fix: { agent: 'a' }, auto_retry: true })
    expect(ap).toEqual({ name: 'OK', kind: 'approval', before: [], message: 'm' })
  })
  it('drops a fixer that is not in the check project once the agents are known', () => {
    const base = { name: 'T', kind: 'gate', project: 'p1', run: ['pytest -q'], timeout_min: 60, fix: { agent: 'ag2', rounds: 2 } }
    const agents = [{ id: 'ag1', project_id: 'p1' }, { id: 'ag2', project_id: 'p2' }]
    expect('fix' in stagePayload(base, agents)).toBe(false)
    expect(stagePayload({ ...base, fix: { agent: 'ag1', rounds: 2 } }, agents).fix).toEqual({ agent: 'ag1', rounds: 2 })
    // agents not loaded: the server decides
    expect(stagePayload(base, []).fix).toEqual({ agent: 'ag2', rounds: 2 })
  })
  it('budget only when set; null only to clear a saved one', () => {
    expect(budgetPayload(5, null)).toEqual({ budget_usd: 5 })
    expect(budgetPayload(null, null)).toEqual({})
    expect(budgetPayload(undefined, undefined)).toEqual({})
    expect(budgetPayload(null, 3)).toEqual({ budget_usd: null })
  })
  it('lists the names earlier steps hand over', () => {
    const stages = [
      { name: 'Plan', items: [{ agent: 'a', outputs: ['plan_path'] }, { agent: 'b', outputs: ['plan_path', 'summary'] }] },
      { name: 'T', kind: 'gate', run: ['x'] },
      { name: 'Dev', items: [{ agent: 'c', outputs: ['branch'] }] },
    ]
    expect(earlierOutputs(stages, 0)).toEqual([])
    expect(earlierOutputs(stages, 2).map((o) => o.text)).toEqual(['{{stage1.plan_path}}', '{{stage1.summary}}'])
    expect(earlierOutputs(stages, 3).map((o) => o.text)).toEqual(['{{stage1.plan_path}}', '{{stage1.summary}}', '{{stage3.branch}}'])
  })
})

describe('approval ahead round trip', () => {
  it('keeps ahead only when on', () => {
    expect(stagePayload(formStage({ name: 'Plan', kind: 'approval', message: 'm', ahead: true }), [])).toMatchObject({ ahead: true })
    expect(stagePayload(formStage({ name: 'Rel', kind: 'approval', message: 'm' }), [])).not.toHaveProperty('ahead')
    expect(stagePayload(formStage({ name: 'Rel', kind: 'approval', message: 'm', ahead: false }), [])).not.toHaveProperty('ahead')
  })
})
