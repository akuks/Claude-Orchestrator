import { describe, expect, it } from 'vitest'
import { appendPlaceholder, plainServerText, checkCommand, exportFileName, formStage, runStatusText, runStepText, stageLabel, stagePayload } from './gates'

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
