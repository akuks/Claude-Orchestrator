// Resume helpers (pure): the notices above a run's log, the step label and
// who may Continue. Handoff and notes text is agent- or repo-derived: callers
// show it as plain text only.
import { revealHidden } from '../guard/guard'

const ACTIVE = ['queued', 'running', 'awaiting_approval']
const TERMINAL = ['completed', 'failed', 'cancelled']

const payloadOf = (e) => (e && e.payload) || {}

// The notices for a run: [{kind, text}], oldest cause first. (`task` is kept for the call shape.)
export function notices(task, events, handoff) {
  const out = []
  const evs = Array.isArray(events) ? events : []
  for (const e of evs) {
    const p = payloadOf(e)
    if (e.type === 'recovery') {
      if (p.gave_up) {
        out.push({
          kind: 'gave_up',
          text: `Gave up after ${p.recoveries ?? 'several'} interrupted runs: ${p.why || 'the run was cut off'}.`,
        })
      } else {
        const n = Number(p.in_flight) || 0
        out.push({
          kind: 'recovered',
          text:
            `Recovered after an interruption (${p.why || 'the run was cut off'})` +
            (n ? `; ${n} action${n === 1 ? ' was' : 's were'} in flight` : '') +
            (p.orphan_killed ? '; the old process was stopped' : '') +
            '. The agent was told what was running.',
        })
      }
    } else if (e.type === 'repo_changed') {
      out.push({
        kind: 'repo_changed',
        text:
          p.message ||
          'This folder now holds a different repository than before (it may have been cloned again). Decisions and the code map may not apply.',
      })
    } else if (e.type === 'fresh_session' && p.credentials_changed === true) {
      out.push({
        kind: 'credentials_changed',
        text: 'Credentials changed since the earlier session, so this step started fresh with the handoff.',
      })
    }
  }
  const c = (handoff && handoff.content) || {}
  const commits = Array.isArray(c.outside_commits) ? c.outside_commits : []
  if (c.history_rewritten) {
    out.push({
      kind: 'history_rewritten',
      text: 'Git history changed since the last save; commits made outside this thread are unknown.',
    })
  } else if (commits.length) {
    out.push({
      kind: 'outside_commits',
      text: `${commits.length} commit${commits.length === 1 ? ' was' : 's were'} made outside this thread since its last step.`,
    })
  }
  if (c.no_commits) {
    out.push({
      kind: 'no_commits',
      text: 'This repo has no commits yet, so a bad edit cannot be undone with git until the first commit.',
    })
  }
  if (Array.isArray(c.outside_docs) && c.outside_docs.length) {
    out.push({
      kind: 'temp_files',
      text: `${c.outside_docs.length} file${c.outside_docs.length === 1 ? ' is' : 's are'} in a temp folder and may vanish.`,
    })
  }
  return out
}

// "Step 2 of 4: …" from a handoff content.step ({n, m, text}), else null.
export function stepLabel(step) {
  if (!step || step.n == null || step.m == null) return null
  const text = revealHidden(step.text || '').text
  return `Step ${step.n} of ${step.m}${text ? `: ${text}` : ''}`
}

// Who may Continue a step. The server decides who may act (task.can_manage) and who owns the session
// (task.can_resume); a 409 is still shown. `user` is kept for the call shape only.
// eslint-disable-next-line no-unused-vars
export function canContinue(task, user, thread) {
  const none = { resume: false, fresh: false }
  if (!task || !TERMINAL.includes(task.status) || task.origin === 'agent') return none
  if ((thread || []).some((t) => ACTIVE.includes(t.status))) return none
  const manage = task.can_manage === true
  return { resume: manage && task.can_resume === true && !!task.session_id, fresh: manage }
}
