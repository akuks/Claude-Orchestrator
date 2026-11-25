import { describe, expect, it } from 'vitest'
import { plainError } from './plainError'

const e = (status, message) => Object.assign(new Error(message), { status })

describe('plainError', () => {
  it('passes a 409 and a 400 sentence through, hides the rest', () => {
    expect(plainError(e(409, 'Only now.'))).toBe('Only now.')
    expect(plainError(e(400, 'Only a failed run can be retried'))).toBe('Only a failed run can be retried')
    expect(plainError(e(403, 'x'))).toBe("You don't have permission to do this.")
    expect(plainError(e(500, 'Traceback'))).toBe("That didn't work. Try again.")
    expect(plainError(new Error('boom'))).toBe("That didn't work. Try again.")
  })
})
