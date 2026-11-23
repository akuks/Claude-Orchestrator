import { describe, expect, it } from 'vitest'
import { checkCommand, exportFileName, formStage, stageLabel, stagePayload } from './gates'

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
